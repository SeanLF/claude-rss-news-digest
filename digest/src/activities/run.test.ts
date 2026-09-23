import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MockActivityEnvironment } from "@temporalio/testing";
import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { freshDb } from "../store/test-db.js";
import { runActivities } from "./run.js";

const RSS = `<rss version="2.0"><channel><title>T</title>
<item><title>Old</title><link>https://f.test/old</link><pubDate>Thu, 18 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>New</title><link>https://f.test/new</link><pubDate>Thu, 18 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>`;

async function setup(body = RSS) {
  const url = await freshDb([]);
  const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
  writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }, { id: "p", name: "P", url: "https://p.test/rss", bias: "center", factuality: "high", perspective: "global", active: false, inactive_reason: "blocked" }]));
  let calls = 0;
  const fake = (() => {
    calls++;
    return Promise.resolve(new Response(body));
  }) as unknown as typeof fetch;
  const store = new ArtifactStore(url);
  return { url, db: openDb(url), store, acts: runActivities({ store, dbUrl: url, sourcesFile, fetch: fake }), calls: () => calls };
}

describe("run lifecycle", () => {
  it("starts a run with its source list, fetches newer entries once, and finishes only when sent", async () => {
    const { db, store, acts, calls } = await setup();
    await db.exec("INSERT INTO digest_runs (run_at, completed_at, status, outcome) VALUES ('2026-09-18 10:25:40', '2026-09-18 10:42:40', 'completed', 'sent')");
    const { runId, sourceIds, lastRun } = await acts.startRun({ runDate: "2026-09-19" });
    expect(sourceIds).toEqual(["f"]);
    expect(lastRun).toBe("2026-09-18 10:25:40");
    expect(await store.content(runId, "sources.csv")).toBe("id,name,bias,factuality,perspective\nf,F,center,high,global\n");
    expect(await acts.fetchFeed(runId, "f", lastRun)).toEqual({ sourceId: "f", ok: true, fetched: 2, kept: 1 });
    expect(await acts.fetchFeed(runId, "f", lastRun)).toEqual({ sourceId: "f", ok: true, fetched: 2, kept: 1 }); // resume: no refetch
    expect(calls()).toBe(1);
    expect(await db.all("SELECT title FROM fetched_articles WHERE run_id=$1", [runId])).toEqual([{ title: "New" }]);
    await acts.finishRun(runId, { stories: 0, broadcast: "rejected" });
    expect(await db.one("SELECT completed_at, status, outcome FROM digest_runs WHERE id=$1", [runId])).toEqual({ completed_at: null, status: "completed", outcome: "rejected" });
    // A rejected run may be resumed, and then sent.
    await db.run("UPDATE digest_runs SET status='running', outcome=NULL WHERE id=$1", [runId]);
    await acts.finishRun(runId, { stories: 17, broadcast: "sent", recipients: 12 });
    expect(await db.one("SELECT status, outcome, articles_emailed FROM digest_runs WHERE id=$1", [runId])).toEqual({ status: "completed", outcome: "sent", articles_emailed: 12 });
  });
  it("a feed that cannot be reached records a failed health row on its last attempt", async () => {
    const url = await freshDb([]);
    const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
    writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }]));
    const down = (() => Promise.reject(new Error("ECONNREFUSED"))) as unknown as typeof fetch;
    const acts = runActivities({ store: new ArtifactStore(url), dbUrl: url, sourcesFile, fetch: down });
    const { runId } = await acts.startRun({ runDate: "2026-09-19" });
    expect(await acts.fetchFeed(runId, "f", null)).toMatchObject({ ok: false, fetched: 0, kept: 0 });
    expect(await openDb(url).one("SELECT success, error_message AS e FROM source_health WHERE run_id=$1", [runId])).toMatchObject({ success: false });
  });
  it("a resume without its sources.csv fails rather than fetching today's catalogue", async () => {
    const { db, acts } = await setup();
    await db.exec("INSERT INTO digest_runs (id, run_at) VALUES (7, '2026-09-18 10:25:40')");
    await expect(acts.startRun({ runDate: "2026-09-18", resumeRun: 7 })).rejects.toMatchObject({ type: "MissingInput" });
  });
  it("a resume from a new execution runs a failed or unsent run again under a new attempt; a sent run stays completed", async () => {
    const { db, store, acts } = await setup();
    await db.exec("INSERT INTO digest_runs (id, run_at, status) VALUES (7, '2026-09-18 10:25:40', 'failed')");
    await db.exec("INSERT INTO digest_runs (id, run_at, completed_at, status, outcome) VALUES (8, '2026-09-19 10:25:40', '2026-09-19 10:45:00', 'completed', 'sent')");
    await db.exec("INSERT INTO digest_runs (id, run_at, status, outcome) VALUES (9, '2026-09-20 10:25:40', 'completed', 'held-out')");
    await db.exec("INSERT INTO run_attempts (run_id, pipeline, workflow_run_id, state) VALUES (7, 'temporal', 'exec-1', 'failed')");
    for (const id of [7, 8, 9]) await store.put(id, "sources.csv", "id,name,bias,factuality,perspective\nf,F,center,high,global\n");
    const env = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-2026-09-18", runId: "exec-2" } });
    await env.run(() => acts.startRun({ runDate: "2026-09-18", resumeRun: 7 }));
    await env.run(() => acts.startRun({ runDate: "2026-09-18", resumeRun: 7 })); // a retry: no second attempt
    await new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-2026-09-19", runId: "exec-3" } }).run(() => acts.startRun({ runDate: "2026-09-19", resumeRun: 8 }));
    await new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-2026-09-20", runId: "exec-4" } }).run(() => acts.startRun({ runDate: "2026-09-20", resumeRun: 9 }));
    expect(await db.all("SELECT id, status, outcome FROM digest_runs ORDER BY id")).toEqual([
      { id: 7, status: "running", outcome: null },
      { id: 8, status: "completed", outcome: "sent" },
      { id: 9, status: "running", outcome: null },
    ]);
    expect(await db.all("SELECT run_id, workflow_id, workflow_run_id, state FROM run_attempts ORDER BY id")).toEqual([
      { run_id: 7, workflow_id: null, workflow_run_id: "exec-1", state: "failed" },
      { run_id: 7, workflow_id: "digest-2026-09-18", workflow_run_id: "exec-2", state: "running" },
      { run_id: 8, workflow_id: "digest-2026-09-19", workflow_run_id: "exec-3", state: "running" },
      { run_id: 9, workflow_id: "digest-2026-09-20", workflow_run_id: "exec-4", state: "running" },
    ]);
  });
  it("finishing a run closes this execution's attempt", async () => {
    const { db, acts } = await setup();
    const env = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-x", runId: "exec-9" } });
    const { runId } = (await env.run(() => acts.startRun({ runDate: "2026-09-19" }))) as { runId: number };
    await env.run(() => acts.finishRun(runId, { stories: 3, broadcast: "sent", recipients: 2 }));
    expect(await db.one("SELECT state, ended_at IS NOT NULL AS ended FROM run_attempts WHERE workflow_run_id='exec-9'")).toEqual({ state: "finished", ended: true });
  });
  describe("the cross-pipeline guard: one digest per day, whichever pipeline started it", () => {
    const today = new Date().toISOString().slice(0, 10);
    it("refuses a day that already has a completed run", async () => {
      const { db, acts } = await setup();
      await db.exec(`INSERT INTO digest_runs (run_at, completed_at, status, outcome) VALUES ('${today} 10:25:40', '${today} 10:45:00', 'completed', 'sent')`);
      await expect(acts.startRun({ runDate: today })).rejects.toMatchObject({ type: "AlreadyRan", nonRetryable: true });
    });
    it("refuses while another run of the day started within the last 4 h and is still running", async () => {
      const { db, acts } = await setup();
      await db.exec("INSERT INTO digest_runs (run_at, status) VALUES (now() - interval '30 minutes', 'running')");
      await expect(acts.startRun({ runDate: "" })).rejects.toMatchObject({ type: "AlreadyRan" });
    });
    it("starts over a run that failed, or one still marked running after 4 h (a crash)", async () => {
      const { db, acts } = await setup();
      await db.exec("INSERT INTO digest_runs (run_at, status) VALUES (now() - interval '10 minutes', 'failed')");
      await db.exec("INSERT INTO digest_runs (run_at, status) VALUES (now() - interval '5 hours', 'running')");
      await db.exec("DELETE FROM digest_runs WHERE status = 'running' AND (run_at AT TIME ZONE 'UTC')::date <> (now() AT TIME ZONE 'UTC')::date");
      await expect(acts.startRun({ runDate: "" })).resolves.toMatchObject({ sourceIds: ["f"] });
    });
    it("a retry of the same execution after its INSERT committed gets the same run back, not AlreadyRan", async () => {
      const url = await freshDb([]);
      const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
      writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }]));
      const real = new ArtifactStore(url);
      let failNext = true;
      // Fails once, after startRun's INSERT has committed: the retry is what this test is about.
      const store = new Proxy(real, {
        get(target, prop, receiver) {
          if (prop === "put" && failNext)
            return () => {
              failNext = false;
              return Promise.reject(new Error("worker lost after the insert"));
            };
          const v = Reflect.get(target, prop, receiver) as unknown;
          return typeof v === "function" ? (v as (...a: unknown[]) => unknown).bind(target) : v;
        },
      });
      const acts = runActivities({ store, dbUrl: url, sourcesFile });
      const env = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-scheduled", runId: "exec-1" } });
      await expect(env.run(() => acts.startRun({ runDate: "" }))).rejects.toThrow(/worker lost/);
      const retried = (await env.run(() => acts.startRun({ runDate: "" }))) as Awaited<ReturnType<typeof acts.startRun>>;
      const db = openDb(url);
      expect(await db.all("SELECT id FROM digest_runs")).toEqual([{ id: retried.runId }]);
      expect(await db.all("SELECT run_id, workflow_run_id FROM run_attempts")).toEqual([{ run_id: retried.runId, workflow_run_id: "exec-1" }]);
      expect(retried.sourceIds).toEqual(["f"]);
      // A different execution the same day is still refused while that run is running.
      const other = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-manual", runId: "exec-2" } });
      await expect(other.run(() => acts.startRun({ runDate: "" }))).rejects.toMatchObject({ type: "AlreadyRan" });
    });
    it("two starts of the same day at once: one run, the other refused", async () => {
      const { db, acts } = await setup();
      const start = (exec: string) => new MockActivityEnvironment({ workflowExecution: { workflowId: `digest-${exec}`, runId: exec } }).run(() => acts.startRun({ runDate: "" }));
      const results = await Promise.allSettled([start("a"), start("b")]);
      expect(results.map((r) => r.status).toSorted()).toEqual(["fulfilled", "rejected"]);
      expect(await db.one("SELECT count(*) AS n FROM digest_runs")).toEqual({ n: 1 });
    });
    it("force starts regardless (the successor of --force)", async () => {
      const { db, acts } = await setup();
      await db.exec(`INSERT INTO digest_runs (run_at, completed_at, status, outcome) VALUES ('${today} 10:25:40', '${today} 10:45:00', 'completed', 'sent')`);
      await expect(acts.startRun({ runDate: today, force: true })).resolves.toMatchObject({ sourceIds: ["f"] });
    });
  });
  it("records a feed that does not parse as a failed source instead of throwing", async () => {
    const { acts } = await setup("<html>not a feed</html>");
    const { runId } = await acts.startRun({ runDate: "2026-09-19" });
    const r = await acts.fetchFeed(runId, "f", null);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/parse error/);
  });
});
