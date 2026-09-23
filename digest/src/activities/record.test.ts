import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
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

async function setup(selections: unknown = SELECTIONS) {
  const url = await migratedDb([{ id: 300, runAt: "2026-09-18 10:25:40" }]);
  const store = new ArtifactStore(url);
  const sel = await store.put(300, "selections.json", JSON.stringify(selections, null, 2));
  await store.put(300, "article_index.json", JSON.stringify(INDEX));
  const clusters = await store.put(300, "clusters.json", JSON.stringify({ clusters: [{ story: "c1", articles: ["A1", "A2"] }] }));
  const html = await store.put(300, "digest.html", PAGE);
  return { url, store, sel, clusters, html, db: openDb(url), acts: recordActivities({ store, dbUrl: url }) };
}

describe("shownHeadlines (render.extract_headlines)", () => {
  it("is one row per source per story, in order, after ids resolve", () => {
    const rows = shownHeadlines(SELECTIONS, INDEX);
    expect(rows).toEqual([
      { headline: "Deal signed", tier: "must_know", source_id: "reuters", source_title: "Deal signed - Reuters", cluster_id: "c1" },
      { headline: "Deal signed", tier: "must_know", source_id: "bbc", source_title: "Deal is signed", cluster_id: "c1" },
      { headline: "Yen falls", tier: "should_know", source_id: "nhk", source_title: "Yen slides", cluster_id: "c2" },
    ]);
  });
});

describe("record activities", () => {
  it("archiveRun writes nothing: the selections and clusters are already the run's artifacts", async () => {
    const { acts, sel, clusters, store } = await setup();
    const before = await store.names(300);
    await acts.archiveRun(300, sel, clusters);
    expect(await store.names(300)).toEqual(before);
  });
  it("publishes the web copy of the issue as the day's first revision, with its preheader, stripped of the inbox-only parts", async () => {
    const { acts, sel, html, db } = await setup();
    expect(await acts.saveDigest(300, html, sel)).toEqual({ date: "2026-09-18" });
    const row = await db.one<Record<string, unknown>>("SELECT issue_date, revision, run_id, preheader, html FROM issues");
    expect(row).toMatchObject({ issue_date: "2026-09-18", revision: 1, run_id: 300, preheader: "Deal signed; yen falls." });
    expect(row!["html"]).not.toContain("preheader");
    expect(row!["html"]).toContain('<div class="paper">');
    expect(await db.all("SELECT run_id FROM published_runs")).toEqual([{ run_id: 300 }]);
  });
  it("a retried save of the same page adds nothing, and an empty preheader keeps the previous one", async () => {
    const { acts, sel, html, db, store } = await setup();
    await acts.saveDigest(300, html, sel);
    await acts.saveDigest(300, html, sel);
    const bare = await store.put(300, "selections.bare.json", JSON.stringify({ ...SELECTIONS, preheader: "" }));
    await acts.saveDigest(300, html, bare);
    expect(await db.all("SELECT revision, preheader FROM issues")).toEqual([{ revision: 1, preheader: "Deal signed; yen falls." }]);
  });
  it("a forced re-run of a published day adds a revision and leaves the day's send as it was", async () => {
    const { acts, sel, html, db, store } = await setup();
    await acts.saveDigest(300, html, sel);
    await db.exec("INSERT INTO sends (issue_date, run_id, revision, status, resend_id, recipients) VALUES ('2026-09-18', 300, 1, 'sent', 'b1', 12)");
    await db.exec("INSERT INTO runs (id, started_at) VALUES (301, '2026-09-18 14:00:00')");
    const page = await store.put(301, "digest.html", PAGE.replace("m</p>", "m2</p>"));
    const sel2 = await store.put(301, "selections.json", JSON.stringify(SELECTIONS));
    await acts.saveDigest(301, page, sel2);
    expect(await db.all("SELECT revision, run_id FROM issues ORDER BY revision")).toEqual([{ revision: 1, run_id: 300 }, { revision: 2, run_id: 301 }]);
    expect(await db.one("SELECT revision, status, recipients FROM sends")).toEqual({ revision: 1, status: "sent", recipients: 12 });
  });
  it("records the shown headlines once per run, for the next day's dedup", async () => {
    const { acts, sel, db } = await setup();
    expect(await acts.recordShownHeadlines(300, sel)).toEqual({ rows: 3 });
    expect(await acts.recordShownHeadlines(300, sel)).toEqual({ rows: 3 });
    expect(await db.all("SELECT headline, tier, source_id, source_title, cluster_id, run_id FROM story_sources ORDER BY id")).toEqual([
      { headline: "Deal signed", tier: "must_know", source_id: "reuters", source_title: "Deal signed - Reuters", cluster_id: "c1", run_id: 300 },
      { headline: "Deal signed", tier: "must_know", source_id: "bbc", source_title: "Deal is signed", cluster_id: "c1", run_id: 300 },
      { headline: "Yen falls", tier: "should_know", source_id: "nhk", source_title: "Yen slides", cluster_id: "c2", run_id: 300 },
    ]);
  });
  it("refuses to record shown headlines it cannot resolve to sources, rather than writing rows with no source", async () => {
    const url = await migratedDb([{ id: 301, runAt: "2026-09-19 10:25:40" }]);
    const store = new ArtifactStore(url);
    const sel = await store.put(301, "selections.json", JSON.stringify(SELECTIONS));
    await expect(recordActivities({ store, dbUrl: url }).recordShownHeadlines(301, sel)).rejects.toThrow(/article_index.json/);
    expect(await openDb(url).one("SELECT COUNT(*) AS n FROM story_sources")).toEqual({ n: 0 });
  });
  it("marks a running run failed with its error, keeping its rows, and its attempt failed", async () => {
    const { acts, db, sel, html } = await setup();
    await db.exec("INSERT INTO run_attempts (run_id, pipeline) VALUES (300, 'temporal')");
    await acts.saveDigest(300, html, sel);
    await acts.abortRun(300, "ActivityFailure: boom");
    expect(await db.one("SELECT status, outcome, error FROM runs WHERE id=300")).toEqual({ status: "failed", outcome: null, error: "ActivityFailure: boom" });
    expect(await db.one("SELECT status, error FROM run_attempts WHERE run_id=300")).toEqual({ status: "failed", error: "ActivityFailure: boom" });
    expect(await db.one("SELECT COUNT(*) AS n FROM issues")).toEqual({ n: 1 });
  });
  it("never fails a completed run: a sent run stays sent, and the attempt carries the error", async () => {
    const { acts, db } = await setup();
    await db.exec("UPDATE runs SET status='completed', outcome='sent' WHERE id=300");
    await acts.abortRun(300, "late failure");
    expect(await db.one("SELECT status, outcome, error FROM runs WHERE id=300")).toEqual({ status: "completed", outcome: "sent", error: null });
  });
});

describe("finishRun", () => {
  it("a run with the send disabled completed without delivery: its outcome says why", async () => {
    const { url, store, db } = await setup();
    const run = runActivities({ store, dbUrl: url, sourcesFile: "/dev/null" });
    await run.finishRun(300, { stories: 17, broadcast: "disabled", recipients: 0 });
    expect(await db.one("SELECT status, outcome FROM runs WHERE id=300")).toEqual({ status: "completed", outcome: "disabled" });
  });
  it("a sent run completes with its fetch-time kept count", async () => {
    const { url, store, db } = await setup();
    await db.exec("INSERT INTO runs (id, started_at) VALUES (299, '2026-09-17 10:25:40')");
    await db.exec("INSERT INTO source_fetches (source_id, is_success, articles_fetched, articles_kept, run_id) VALUES ('a', true, 40, 30, 300), ('b', true, 20, 12, 300), ('c', false, 0, 0, 300), ('a', true, 9, 9, 299)");
    const run = runActivities({ store, dbUrl: url, sourcesFile: "/dev/null" });
    await run.finishRun(300, { stories: 17, broadcast: "sent", recipients: 12 });
    expect(await db.one("SELECT status, outcome, articles_kept FROM runs WHERE id=300")).toEqual({ status: "completed", outcome: "sent", articles_kept: 42 });
  });
});
