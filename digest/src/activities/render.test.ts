import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { loadAssets } from "../render/render.js";
import { ArtifactStore, type Pointer } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { EMAIL_OUTPUT, RENDER_CONTEXT, renderActivity, WEB_OUTPUT } from "./render.js";

const REPO = new URL("../../../", import.meta.url).pathname;
const assets = loadAssets({ templates: `${REPO}newsroom/templates`, design: `${REPO}design` });
const env = { digestName: "Digest", digestDomain: "news.example", archiveUrl: "https://news.example", authorName: "Sean", authorUrl: "https://author.example" };
const stub = (runId: number, name: string): Pointer => ({ runId, name, sha256: "0".repeat(64) });

function setup() {
  const dbPath = freshDb([300]);
  const db = new DatabaseSync(dbPath);
  db.exec("CREATE TABLE digests (date TEXT PRIMARY KEY, html TEXT NOT NULL)");
  for (const d of ["2026-09-16", "2026-09-17"]) db.prepare("INSERT INTO digests (date, html) VALUES (?, '')").run(d);
  db.close();
  const store = new ArtifactStore(dbPath);
  const selections = store.put(300, "selections.json", JSON.stringify({ must_know: [{ headline: "Russia votes", summary: "Voting began.", why_it_matters: "It matters.", cluster_id: "duma", sources: [{ article_id: "A1" }] }], should_know: [{ headline: "Yen falls", summary: "The yen fell.", sources: [{ article_id: "A2" }] }], preheader: "Russia votes" }));
  store.put(300, "article_index.json", JSON.stringify({
    A1: { name: "Reuters", url: "https://news.google.com/rss/articles/X", bias: "center", source_id: "reuters", original_title: "Russia votes" },
    A2: { name: "BBC", url: "https://www.bbc.co.uk/news/yen", bias: "center", source_id: "bbc", original_title: "Yen falls" },
  }));
  let clock = new Date("2026-09-18T10:42:40Z");
  const activity = renderActivity({ store, dbPath, assets, env, now: () => clock });
  return { store, selections, activity, tick: () => (clock = new Date("2026-09-18T11:59:00Z")) };
}

describe("render activity", () => {
  it("renders the web issue and the email into the store, with no thread context or decoded links when those stages have not run", async () => {
    const { store, selections, activity } = setup();
    const out = await activity(300, selections, stub(300, "thread_context.json"), stub(300, "gnews_links.json"));
    expect(out.html.name).toBe(WEB_OUTPUT);
    expect(out.email.name).toBe(EMAIL_OUTPUT);
    const web = store.get(out.html);
    expect(web).toContain("No. 3<br>Filed 10:42 UTC");
    expect(web).toContain('<a href="https://news.google.com/rss/articles/X">1</a>');
    expect(web).not.toContain("Ongoing");
    expect(store.get(out.email)).toContain("View in browser");
  });
  it("takes thread context and decoded links when their stages wrote them", async () => {
    const { store, selections, activity } = setup();
    const threads = store.put(300, "thread_context.json", JSON.stringify({ duma: { thread_id: 889, day: 2, delta: "Polls opened.", url: "https://news.example/thread/889" } }));
    const gnews = store.put(300, "gnews_links.json", JSON.stringify({ "https://news.google.com/rss/articles/X": "https://www.reuters.com/world/russia-votes" }));
    const web = store.get((await activity(300, selections, threads, gnews)).html);
    expect(web).toContain('<a href="https://news.example/thread/889"');
    expect(web).toContain("Polls opened.");
    expect(web).toContain('<a href="https://www.reuters.com/world/russia-votes">1</a>');
  });
  it("is idempotent on its output: a retry renders at the first attempt's time and returns the same pointers", async () => {
    const { store, selections, activity, tick } = setup();
    const first = await activity(300, selections, stub(300, "thread_context.json"), stub(300, "gnews_links.json"));
    tick();
    const again = await activity(300, selections, stub(300, "thread_context.json"), stub(300, "gnews_links.json"));
    expect(again).toEqual(first);
    expect(JSON.parse(store.get(store.find(300, RENDER_CONTEXT)!))).toEqual({ renderedAt: "2026-09-18T10:42:40.000Z", issueNo: 3 });
  });
  it("re-renders changed selections, quarantining the stale renders", async () => {
    const { store, activity } = setup();
    const a = store.put(300, "selections.json.v1", JSON.stringify({ must_know: [{ headline: "One", summary: "s", sources: [{ article_id: "A2" }] }], should_know: [] }));
    await activity(300, a, stub(300, "thread_context.json"), stub(300, "gnews_links.json"));
    const b = store.put(300, "selections.json.v2", JSON.stringify({ must_know: [{ headline: "Two", summary: "s", sources: [{ article_id: "A2" }] }], should_know: [] }));
    const out = await activity(300, b, stub(300, "thread_context.json"), stub(300, "gnews_links.json"));
    expect(store.get(out.html)).toContain("Two");
    expect(store.find(300, `${WEB_OUTPUT}.corrupt.1`)).toBeDefined();
    expect(store.find(300, `${EMAIL_OUTPUT}.corrupt.1`)).toBeDefined();
  });
});
