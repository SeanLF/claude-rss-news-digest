import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { candidateIds, FULLTEXT_HEALTH, FULLTEXT_OUTPUT, fulltextActivities } from "./fulltext.js";

const selected = {
  must_know: [{ cluster_index: 1, article_ids: ["A1", "A2", "A3", "A4"] }],
  should_know: [{ cluster_index: 2, article_ids: ["A2", "A5"] }, "junk"],
};
const index = { A1: { url: "https://a.com/1" }, A2: { url: "https://b.com/2" }, A3: {}, A5: { url: "https://c.com/5" } };

function setup(enabled = true) {
  const store = new ArtifactStore(freshDb([300]));
  const sel = store.put(300, "selected.json", JSON.stringify(selected));
  store.put(300, "article_index.json", JSON.stringify(index));
  return { store, sel, acts: fulltextActivities({ store, perStory: 3, enabled }) };
}

describe("fulltext", () => {
  it("takes the first perStory ids of every story, deduped, in SELECT's order (fulltext._candidate_article_ids)", () => {
    expect(candidateIds(selected, 3)).toEqual(["A1", "A2", "A3", "A5"]);
  });
  it("plans a task for every candidate with a URL", async () => {
    const { sel, acts } = setup();
    expect(await acts.planFulltext(300, sel)).toEqual({ tasks: [["A1", "https://a.com/1"], ["A2", "https://b.com/2"], ["A5", "https://c.com/5"]] });
  });
  it("plans nothing when the output already exists, unless forced", async () => {
    const { store, sel, acts } = setup();
    const done = store.put(300, FULLTEXT_OUTPUT, "{}");
    expect(await acts.planFulltext(300, sel)).toEqual({ tasks: [], existing: done });
    expect((await acts.planFulltext(300, sel, true)).tasks).toHaveLength(3);
  });
  it("a stored attempt that did not complete is quarantined and planned again, so a resume retries it", async () => {
    const { store, sel, acts } = setup();
    await acts.storeFulltext(300, { tasks: 3, results: {}, outcome: "unavailable" });
    const plan = await acts.planFulltext(300, sel);
    expect(plan.tasks).toHaveLength(3);
    expect(plan.existing).toBeUndefined();
    expect(store.find(300, FULLTEXT_OUTPUT)).toBeUndefined();
    expect(store.find(300, `${FULLTEXT_OUTPUT}.corrupt.1`)).toBeDefined();
    await acts.storeFulltext(300, { tasks: 3, results: { A1: "Body text that came back this time." }, outcome: "completed" });
    expect((await acts.planFulltext(300, sel)).existing).toBeDefined();
  });
  it("an archived output with no health record is kept: nothing says it failed", async () => {
    const { store, sel, acts } = setup();
    store.put(300, FULLTEXT_OUTPUT, JSON.stringify({ A1: { text: "archived" } }));
    expect((await acts.planFulltext(300, sel)).existing).toBeDefined();
  });
  it("switched off (FULLTEXT_ENABLED=false), it plans nothing and says why", async () => {
    const { sel, acts } = setup(false);
    expect(await acts.planFulltext(300, sel)).toEqual({ tasks: [], skip: "disabled" });
  });
  it("with no candidates it plans nothing and says why", async () => {
    const { store, acts } = setup();
    const empty = store.put(300, "selected_empty.json", JSON.stringify({ must_know: [], should_know: [] }));
    expect(await acts.planFulltext(300, empty)).toEqual({ tasks: [], skip: "no_candidates" });
  });
  it("stores the texts with links scrubbed, in the archive's shape, and records health", async () => {
    const { store, acts } = setup();
    const p = await acts.storeFulltext(300, { tasks: 3, results: { A1: "Body text, see https://x.com/y for more." }, outcome: "completed" });
    expect(JSON.parse(store.get(p))).toEqual({ A1: { text: "Body text, see [link] for more." } });
    expect(JSON.parse(store.get(store.find(300, FULLTEXT_HEALTH)!))).toEqual({ tasks: 3, extracted: 1, outcome: "completed" });
  });
  it("an unavailable fetcher stores an empty map, so the run goes on without full text", async () => {
    const { store, acts } = setup();
    const p = await acts.storeFulltext(300, { tasks: 3, results: {}, outcome: "unavailable" });
    expect(store.get(p)).toBe("{}");
    expect(JSON.parse(store.get(store.find(300, FULLTEXT_HEALTH)!))).toMatchObject({ extracted: 0, outcome: "unavailable" });
  });
});
