import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { runActivities } from "./run.js";

const RSS = `<rss version="2.0"><channel><title>T</title>
<item><title>Old</title><link>https://f.test/old</link><pubDate>Thu, 18 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>New</title><link>https://f.test/new</link><pubDate>Thu, 18 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>`;

function setup(body = RSS) {
  const path = freshDb([]);
  const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
  writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }, { id: "p", name: "P", url: "https://p.test/rss", bias: "center", factuality: "high", perspective: "global", active: false, inactive_reason: "blocked" }]));
  let calls = 0;
  const fake = (() => {
    calls++;
    return Promise.resolve(new Response(body));
  }) as unknown as typeof fetch;
  const store = new ArtifactStore(path);
  return { path, store, acts: runActivities({ store, dbPath: path, sourcesFile, fetch: fake }), calls: () => calls };
}

describe("run lifecycle", () => {
  it("starts a run with its source list, fetches newer entries once, and finishes only when sent", async () => {
    const { path, store, acts, calls } = setup();
    const db = new DatabaseSync(path);
    db.exec("INSERT INTO digest_runs (id, run_at, completed_at) VALUES (1, '2026-09-18 10:25:40', '2026-09-18 10:42:40')");
    const { runId, sourceIds, lastRun } = await acts.startRun({ runDate: "2026-09-19" });
    expect(sourceIds).toEqual(["f"]);
    expect(lastRun).toBe("2026-09-18 10:25:40");
    expect(store.get(store.find(runId, "sources.csv")!)).toBe("id,name,bias,factuality,perspective\nf,F,center,high,global\n");
    expect(await acts.fetchFeed(runId, "f", lastRun)).toEqual({ sourceId: "f", ok: true, fetched: 2, kept: 1 });
    expect(await acts.fetchFeed(runId, "f", lastRun)).toEqual({ sourceId: "f", ok: true, fetched: 2, kept: 1 }); // resume: no refetch
    expect(calls()).toBe(1);
    expect(db.prepare("SELECT title FROM fetched_articles WHERE run_id=?").all(runId)).toEqual([{ title: "New" }]);
    await acts.finishRun(runId, { stories: 0, broadcast: "rejected" });
    expect(db.prepare("SELECT completed_at, status FROM digest_runs WHERE id=?").get(runId)).toEqual({ completed_at: null, status: "rejected" });
    await acts.finishRun(runId, { stories: 12, broadcast: "sent" });
    expect(db.prepare("SELECT status, articles_emailed FROM digest_runs WHERE id=?").get(runId)).toEqual({ status: "completed", articles_emailed: 12 });
  });
  it("a feed that cannot be reached records a failed health row on its last attempt", async () => {
    const path = freshDb([]);
    const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
    writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }]));
    const down = (() => Promise.reject(new Error("ECONNREFUSED"))) as unknown as typeof fetch;
    const acts = runActivities({ store: new ArtifactStore(path), dbPath: path, sourcesFile, fetch: down });
    const { runId } = await acts.startRun({ runDate: "2026-09-19" });
    expect(await acts.fetchFeed(runId, "f", null)).toMatchObject({ ok: false, fetched: 0, kept: 0 });
    expect(new DatabaseSync(path).prepare("SELECT success, error_message AS e FROM source_health WHERE run_id=?").get(runId)).toMatchObject({ success: 0 });
  });
  it("a resume without its sources.csv fails rather than fetching today's catalogue", async () => {
    const { path, acts } = setup();
    new DatabaseSync(path).exec("INSERT INTO digest_runs (id, run_at) VALUES (7, '2026-09-18 10:25:40')");
    await expect(acts.startRun({ runDate: "2026-09-18", resumeRun: 7 })).rejects.toMatchObject({ type: "MissingInput" });
  });
  it("records a feed that does not parse as a failed source instead of throwing", async () => {
    const { acts } = setup("<html>not a feed</html>");
    const { runId } = await acts.startRun({ runDate: "2026-09-19" });
    const r = await acts.fetchFeed(runId, "f", null);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/parse error/);
  });
});
