import { afterEach, describe, expect, it, vi } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { GNEWS_HEALTH, gnewsActivities, isGnewsUrl, survivingLinks } from "./gnews.js";
import { DECODED_LINKS } from "./index.js";

const GN = (token: string) => `https://news.google.com/rss/articles/${token}?oc=5`;
const entry = (name: string, url: string, title: string) => ({ name, url, bias: "center", source_id: name.toLowerCase(), original_title: title });
const index = {
  A1: entry("Reuters", GN("R1"), "Russia votes - Reuters"),
  A2: entry("BBC", "https://www.bbc.co.uk/news/1", "Russia votes"),
  A3: entry("Reuters", GN("R3"), "Yen falls - Reuters"),
  A4: entry("Nikkei", GN("N4"), "Yen falls further"),
  A5: entry("Reuters", GN("R5"), "Unshown story - Reuters"),
  A6: entry("Reuters", GN("R6"), "Yen falls - Reuters"),
};
// A5 is in the index but no story cites it; A6 is a verbatim repost of A3 that collapse drops.
const selections = {
  must_know: [{ headline: "Russia votes", sources: [{ article_id: "A1" }, { article_id: "A2" }] }],
  should_know: [
    { headline: "Yen falls", sources: [{ article_id: "A3" }, { article_id: "A6" }, { article_id: "A4" }] },
    { headline: "Again", sources: [{ article_id: "A1" }] },
    { headline: "Gone", sources: [{ article_id: "A99" }] },
  ],
};

function setup(enabled = true) {
  const store = new ArtifactStore(freshDb([300]));
  const sel = store.put(300, "selections.json", JSON.stringify(selections));
  store.put(300, "article_index.json", JSON.stringify(index));
  return { store, sel, acts: gnewsActivities({ store, enabled }) };
}
function warned() {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  return () => warn.mock.calls.map((c) => String(c[0])).filter((m) => m.includes("decoder contract"));
}
afterEach(() => {
  vi.restoreAllMocks();
});

describe("gnews", () => {
  it("recognises a Google-News article link as gnews.is_gnews_url does", () => {
    expect(isGnewsUrl(GN("X"))).toBe(true);
    expect(isGnewsUrl("https://news.google.com/rss/search?q=reuters")).toBe(false);
    expect(isGnewsUrl("https://www.reuters.com/articles/x")).toBe(false);
    expect(isGnewsUrl("")).toBe(false);
  });
  it("takes only the links the rendered issue shows: resolved, reposts collapsed, deduped, in reading order", () => {
    expect(survivingLinks(selections, index)).toEqual([GN("R1"), GN("R3"), GN("N4")]);
  });
  it("plans the surviving links of the run's selections", async () => {
    const { sel, acts } = setup();
    expect(await acts.planGnews(300, sel)).toEqual({ urls: [GN("R1"), GN("R3"), GN("N4")] });
  });
  it("plans nothing when the run already has its decoded links, unless forced", async () => {
    const { store, sel, acts } = setup();
    const done = await acts.storeGnews(300, { links: 3, decoded: {}, attempted: 3, outcome: "rate_limited" });
    expect(store.find(300, DECODED_LINKS)).toEqual(done);
    expect(await acts.planGnews(300, sel)).toEqual({ urls: [], existing: done });
    expect((await acts.planGnews(300, sel, true)).urls).toHaveLength(3);
  });
  it("an attempt that reached no decoder is quarantined and planned again on a resume", async () => {
    const { store, sel, acts } = setup();
    await acts.storeGnews(300, { links: 3, decoded: {}, attempted: 0, outcome: "unavailable" });
    const plan = await acts.planGnews(300, sel);
    expect(plan.urls).toHaveLength(3);
    expect(plan.existing).toBeUndefined();
    expect(store.find(300, `${DECODED_LINKS}.corrupt.1`)).toBeDefined();
    expect(store.find(300, GNEWS_HEALTH)).toBeUndefined();
  });
  it("switched off (GNEWS_RESOLVE_ENABLED=false), it plans nothing and says why", async () => {
    const { sel, acts } = setup(false);
    expect(await acts.planGnews(300, sel)).toEqual({ urls: [], skip: "disabled" });
  });
  it("with no Google-News links shown it plans nothing and says why", async () => {
    const { store, acts } = setup();
    const direct = store.put(300, "selections_direct.json", JSON.stringify({ must_know: [{ headline: "h", sources: [{ article_id: "A2" }] }], should_know: [] }));
    expect(await acts.planGnews(300, direct)).toEqual({ urls: [], skip: "no_candidates" });
  });
  it("stores the decoded map render reads, and records health", async () => {
    const { store, acts } = setup();
    const p = await acts.storeGnews(300, { links: 3, decoded: { [GN("R1")]: "https://www.reuters.com/world/r1" }, attempted: 3, outcome: "completed" });
    expect(p.name).toBe(DECODED_LINKS);
    expect(JSON.parse(store.get(p))).toEqual({ [GN("R1")]: "https://www.reuters.com/world/r1" });
    expect(JSON.parse(store.get(store.find(300, GNEWS_HEALTH)!))).toEqual({ links: 3, decoded: 1, attempted: 3, outcome: "completed" });
  });
  it("never stores a decoded link a reader could not click", async () => {
    const { store, acts } = setup();
    const p = await acts.storeGnews(300, { links: 2, decoded: { [GN("R1")]: "javascript:alert(1)", [GN("R3")]: "https://www.reuters.com/r3" }, attempted: 2, outcome: "completed" });
    expect(JSON.parse(store.get(p))).toEqual({ [GN("R3")]: "https://www.reuters.com/r3" });
  });
  describe("the canary (digest._resolve_gnews_links)", () => {
    it("warns when enough decodes ran and none of the shown links was upgraded", async () => {
      const calls = warned();
      await setup().acts.storeGnews(300, { links: 3, decoded: {}, attempted: 3, outcome: "completed" });
      expect(calls()).toHaveLength(1);
    });
    it("stays quiet on a 429, which has its own outcome, and under three attempts", async () => {
      const calls = warned();
      const { acts } = setup();
      await acts.storeGnews(300, { links: 3, decoded: {}, attempted: 3, outcome: "rate_limited" });
      await acts.storeGnews(300, { links: 2, decoded: {}, attempted: 2, outcome: "completed" }, true);
      await acts.storeGnews(300, { links: 3, decoded: { [GN("R1")]: "https://www.reuters.com/r1" }, attempted: 3, outcome: "completed" }, true);
      expect(calls()).toHaveLength(0);
    });
  });
});
