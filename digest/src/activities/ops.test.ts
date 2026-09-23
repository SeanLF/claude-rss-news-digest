import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Email } from "../mail/resend.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { opsActivities } from "./ops.js";

// run_health reads tables the store's fresh database leaves out; these are their production shapes,
// cut to the columns the query touches.
function db(): string {
  const path = freshDb([305]);
  const d = new DatabaseSync(path);
  d.exec(`CREATE TABLE shown_narratives (id INTEGER PRIMARY KEY, headline TEXT NOT NULL, tier TEXT, run_id INTEGER, original_title TEXT, shown_at DATETIME DEFAULT (datetime('now', 'utc')));
    CREATE TABLE run_usage (id INTEGER PRIMARY KEY, run_id INTEGER, subagent TEXT NOT NULL, model TEXT NOT NULL);
    CREATE TABLE digests (date TEXT, run_id INTEGER, broadcast_recipients INTEGER);
    CREATE TABLE threads (id INTEGER PRIMARY KEY, status TEXT);
    CREATE TABLE thread_installments (id INTEGER PRIMARY KEY, thread_id INTEGER, run_id INTEGER, matched_score REAL);`);
  d.close();
  return path;
}
const env = { HEALTH_ALERT_EMAIL: "ops@example.com", RESEND_API_KEY: "re_test", RESEND_FROM: "digest@example.com", THREADS_ENABLED: "true" };
afterEach(() => {
  vi.restoreAllMocks();
});

describe("ops activities", () => {
  it("checkRunHealth returns a run-health alert for a violating run and logs the coherence failure kinds", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const path = db();
    const store = new ArtifactStore(path);
    store.put(305, "coherence_report.json", JSON.stringify({ results: [{ pass: false, failed_fields: ["summary"], failure_kinds: { summary: "unsupported" } }] }));
    const ops = opsActivities({ dbPath: path, env, maxAttempts: 3 });
    const req = await ops.checkRunHealth(305, true);
    expect(req).toEqual({ kind: "run-health", runId: 305, violations: ["ZERO_STORIES: the run completed but shipped no stories", "NO_USAGE_RECORDED: no subagent stage recorded usage, so the curation phase left no trace"] });
    expect(log).toHaveBeenCalledWith(JSON.stringify({ stage: "coherence", runId: 305, failureKinds: { contradicted: 0, unsupported: 1, unlabelled: 0 } }));
  });
  it("checkRunHealth is best-effort: a database it cannot read returns no alert instead of failing the run", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const ops = opsActivities({ dbPath: freshDb([305]), env, maxAttempts: 3 }); // no run_usage, no shown_narratives
    expect(await ops.checkRunHealth(305, true)).toBeNull();
    expect(String(err.mock.calls[0]?.[0])).toMatch(/^run-health check FAILED to run for run 305 \(non-fatal\)/);
  });
  it("checkFeeds reads the threshold from HEALTH_ALERT_THRESHOLD", async () => {
    const path = db();
    const d = new DatabaseSync(path);
    for (let i = 0; i < 2; i++) d.prepare("INSERT INTO source_health (source_id, success, run_id) VALUES ('the_hindu', 0, 305)").run();
    d.close();
    expect(await opsActivities({ dbPath: path, env, maxAttempts: 3 }).checkFeeds(305, ["the_hindu"])).toBeNull();
    expect(await opsActivities({ dbPath: path, env: { ...env, HEALTH_ALERT_THRESHOLD: "2" }, maxAttempts: 3 }).checkFeeds(305, ["the_hindu"])).toMatchObject({ kind: "source-health", failing: [["the_hindu", 2]], threshold: 2 });
  });
  it("alert sends through the injected sender", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const sent: Email[] = [];
    const send = (e: Email) => {
      sent.push(e);
      return Promise.resolve({ id: "em" });
    };
    const ops = opsActivities({ dbPath: db(), env, maxAttempts: 3, send });
    await ops.alert({ kind: "archival", runId: 305, failed: ["thread_links"] });
    expect(sent.map((e) => e.subject)).toEqual(["[Alert] Digest archival failed (thread_links)"]);
  });
  it("healthcheck maps success to the bare ping URL and logs through /log", async () => {
    const urls: string[] = [];
    const fetch = ((url: string) => {
      urls.push(url);
      return Promise.resolve(new Response("OK"));
    }) as unknown as typeof globalThis.fetch;
    const ops = opsActivities({ dbPath: db(), env: { HEALTHCHECK_PING_URL: "https://hc-ping.com/u" }, maxAttempts: 3, fetch });
    await ops.healthcheck("start");
    await ops.healthcheck("success");
    await ops.healthcheck("fail");
    await ops.healthcheckLog("curation start");
    expect(urls).toEqual(["https://hc-ping.com/u/start", "https://hc-ping.com/u", "https://hc-ping.com/u/fail", "https://hc-ping.com/u/log"]);
  });
});
