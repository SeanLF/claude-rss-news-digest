import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { migratedDb } from "../store/migrated-db.js";
import { recordActivities, shownHeadlines } from "./record.js";
import { runActivities } from "./run.js";

const SELECTIONS = {
  must_know: [{ headline: "Deal signed", summary: "S.", cluster_id: "c1", sources: [{ article_id: "A1" }, { article_id: "A2" }] }],
  should_know: [{ headline: "Yen falls", summary: "S.", cluster_id: "c2", sources: [{ article_id: "A3" }, { article_id: "A9" }] }],
  preheader: "Deal signed; yen falls.",
};
const INDEX = {
  A1: { name: "Reuters", url: "https://r.test/a", bias: "center", source_id: "reuters", original_title: "Deal signed - Reuters" },
  A2: { name: "BBC", url: "https://b.test/a", bias: "center-left", source_id: "bbc", original_title: "Deal is signed" },
  A3: { name: "NHK", url: "https://n.test/a", bias: "center", source_id: "nhk", original_title: "Yen slides" },
};
const PAGE = `<!DOCTYPE html><html><head><title>T</title></head><body><span class="preheader">Deal signed; yen falls.</span><div class="paper"><p class="footer-meta">m</p></div></body></html>`;

function setup(selections: unknown = SELECTIONS) {
  const path = migratedDb([{ id: 300, runAt: "2026-09-18 10:25:40" }]);
  const store = new ArtifactStore(path);
  const sel = store.put(300, "selections.json", JSON.stringify(selections, null, 2));
  store.put(300, "article_index.json", JSON.stringify(INDEX));
  const clusters = store.put(300, "clusters.json", JSON.stringify({ clusters: [{ story: "c1", articles: ["A1", "A2"] }] }));
  const html = store.put(300, "digest.html", PAGE);
  const db = new DatabaseSync(path);
  return { path, store, sel, clusters, html, db, acts: recordActivities({ store, dbPath: path }) };
}

describe("shownHeadlines (render.extract_headlines)", () => {
  it("is one row per source per story, in order, after ids resolve", () => {
    const rows = shownHeadlines(SELECTIONS, INDEX);
    expect(rows).toEqual([
      { headline: "Deal signed", tier: "must_know", source_id: "reuters", original_title: "Deal signed - Reuters", cluster_id: "c1" },
      { headline: "Deal signed", tier: "must_know", source_id: "bbc", original_title: "Deal is signed", cluster_id: "c1" },
      { headline: "Yen falls", tier: "should_know", source_id: "nhk", original_title: "Yen slides", cluster_id: "c2" },
    ]);
  });
});

describe("record activities", () => {
  it("archives the run's selections and clusters verbatim, once", async () => {
    const { acts, sel, clusters, db, store } = setup();
    await acts.archiveRun(300, sel, clusters);
    await acts.archiveRun(300, sel, clusters);
    expect(db.prepare("SELECT run_id, selections_json AS j FROM selections").all()).toEqual([{ run_id: 300, j: store.get(sel) }]);
    expect(db.prepare("SELECT run_id, clusters_json AS j FROM cluster_runs").all()).toEqual([{ run_id: 300, j: store.get(clusters) }]);
  });
  it("a re-assembled run replaces its archived selections rather than adding a second row", async () => {
    const { acts, sel, clusters, db, store } = setup();
    await acts.archiveRun(300, sel, clusters);
    const again = store.replace(300, "selections.json", JSON.stringify({ ...SELECTIONS, should_know: [] }));
    await acts.archiveRun(300, again, clusters);
    expect(db.prepare("SELECT selections_json AS j FROM selections WHERE run_id=300").all()).toEqual([{ j: store.get(again) }]);
  });
  it("saves the web copy of the issue under the run date, with its preheader, stripped of the inbox-only parts", async () => {
    const { acts, sel, html, db } = setup();
    expect(await acts.saveDigest(300, html, sel)).toEqual({ date: "2026-09-18" });
    const row = db.prepare("SELECT date, run_id, preheader, html, broadcast_id FROM digests").get() as Record<string, unknown>;
    expect(row).toMatchObject({ date: "2026-09-18", run_id: 300, preheader: "Deal signed; yen falls.", broadcast_id: null });
    expect(row["html"]).not.toContain("preheader");
    expect(row["html"]).toContain('<div class="paper">');
  });
  it("a second save keeps the broadcast state, and an empty preheader keeps the first one", async () => {
    const { acts, sel, html, db, store } = setup();
    await acts.saveDigest(300, html, sel);
    db.exec("UPDATE digests SET broadcast_id='b1', broadcast_status='sent', broadcast_recipients=12");
    const bare = store.put(300, "selections.bare.json", JSON.stringify({ ...SELECTIONS, preheader: "" }));
    await acts.saveDigest(300, html, bare);
    expect(db.prepare("SELECT COUNT(*) AS n, preheader, broadcast_id, broadcast_status, broadcast_recipients FROM digests").get()).toEqual({ n: 1, preheader: "Deal signed; yen falls.", broadcast_id: "b1", broadcast_status: "sent", broadcast_recipients: 12 });
  });
  it("records the shown headlines once per run, for the next day's dedup", async () => {
    const { acts, sel, db } = setup();
    expect(await acts.recordShownHeadlines(300, sel)).toEqual({ rows: 3 });
    expect(await acts.recordShownHeadlines(300, sel)).toEqual({ rows: 3 });
    expect(db.prepare("SELECT headline, tier, source_id, original_title, cluster_id, run_id FROM shown_narratives ORDER BY id").all()).toEqual([
      { headline: "Deal signed", tier: "must_know", source_id: "reuters", original_title: "Deal signed - Reuters", cluster_id: "c1", run_id: 300 },
      { headline: "Deal signed", tier: "must_know", source_id: "bbc", original_title: "Deal is signed", cluster_id: "c1", run_id: 300 },
      { headline: "Yen falls", tier: "should_know", source_id: "nhk", original_title: "Yen slides", cluster_id: "c2", run_id: 300 },
    ]);
  });
  it("marks a failed run failed with its error, keeping its rows", async () => {
    const { acts, db, sel, html } = setup();
    await acts.saveDigest(300, html, sel);
    await acts.abortRun(300, "ActivityFailure: boom");
    expect(db.prepare("SELECT status, error, completed_at FROM digest_runs WHERE id=300").get()).toEqual({ status: "failed", error: "ActivityFailure: boom", completed_at: null });
    expect(db.prepare("SELECT COUNT(*) AS n FROM digests").get()).toEqual({ n: 1 });
  });
});

describe("finishRun", () => {
  it("a sent run completes with its recipients emailed and its fetch-time kept count", async () => {
    const { path, store, db } = setup();
    db.exec("INSERT INTO digest_runs (id, run_at) VALUES (299, '2026-09-17 10:25:40')");
    db.exec("INSERT INTO source_health (source_id, success, articles_fetched, articles_kept, run_id) VALUES ('a', 1, 40, 30, 300), ('b', 1, 20, 12, 300), ('c', 0, 0, 0, 300), ('a', 1, 9, 9, 299)");
    const run = runActivities({ store, dbPath: path, sourcesFile: "/dev/null" });
    await run.finishRun(300, { stories: 17, broadcast: "sent", recipients: 12 });
    expect(db.prepare("SELECT status, articles_kept, articles_emailed, completed_at IS NOT NULL AS done FROM digest_runs WHERE id=300").get()).toEqual({ status: "completed", articles_kept: 42, articles_emailed: 12, done: 1 });
  });
});
