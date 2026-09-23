import { afterEach, describe, expect, it, vi } from "vitest";
import type { Email } from "../mail/resend.js";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { freshDb, migratedDb } from "../store/test-db.js";
import { opsActivities } from "./ops.js";

const db = (): Promise<string> => freshDb([305]);
// Nothing listens on port 1: every query fails to connect.
const UNREACHABLE = "postgres://nobody@127.0.0.1:1/none";
const env = { HEALTH_ALERT_EMAIL: "ops@example.com", RESEND_API_KEY: "re_test", RESEND_FROM: "digest@example.com", THREADS_ENABLED: "true" };
afterEach(async () => {
  vi.restoreAllMocks();
});

describe("ops activities", () => {
  it("checkRunHealth returns a run-health alert for a violating run and logs the coherence failure kinds", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const path = await db();
    const store = new ArtifactStore(path);
    await store.put(305, "coherence_report.json", JSON.stringify({ results: [{ pass: false, failed_fields: ["summary"], failure_kinds: { summary: "unsupported" } }] }));
    const ops = opsActivities({ dbUrl: path, env, maxAttempts: 3 });
    const req = await ops.checkRunHealth(305, true);
    expect(req).toEqual({ kind: "run-health", runId: 305, violations: ["ZERO_STORIES: the run completed but shipped no stories", "NO_USAGE_RECORDED: no subagent stage recorded usage, so the curation phase left no trace"] });
    expect(log).toHaveBeenCalledWith(JSON.stringify({ stage: "coherence", runId: 305, failureKinds: { contradicted: 0, unsupported: 1, unlabelled: 0 } }));
  });
  it("checkRunHealth is best-effort: a database it cannot read returns no alert instead of failing the run", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const ops = opsActivities({ dbUrl: UNREACHABLE, env, maxAttempts: 3 });
    expect(await ops.checkRunHealth(305, true)).toBeNull();
    expect(String(err.mock.calls[0]?.[0])).toMatch(/^run-health check FAILED to run for run 305 \(non-fatal\)/);
  });
  it("checkFeeds reads the threshold from HEALTH_ALERT_THRESHOLD", async () => {
    const path = await db();
    for (let i = 0; i < 2; i++) await openDb(path).run("INSERT INTO source_health (source_id, success, run_id) VALUES ('the_hindu', false, 305)");
    expect(await opsActivities({ dbUrl: path, env, maxAttempts: 3 }).checkFeeds(305, ["the_hindu"])).toBeNull();
    expect(await opsActivities({ dbUrl: path, env: { ...env, HEALTH_ALERT_THRESHOLD: "2" }, maxAttempts: 3 }).checkFeeds(305, ["the_hindu"])).toMatchObject({ kind: "source-health", failing: [["the_hindu", 2]], threshold: 2 });
  });
  it("alert sends through the injected sender", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const sent: Email[] = [];
    const send = (e: Email) => {
      sent.push(e);
      return Promise.resolve({ id: "em" });
    };
    const ops = opsActivities({ dbUrl: await db(), env, maxAttempts: 3, send });
    await ops.alert({ kind: "archival", runId: 305, failed: ["thread_links"] });
    expect(sent.map((e) => e.subject)).toEqual(["[Alert] Digest archival failed (thread_links)"]);
  });
  it("a run-failed alert reads the day's broadcast state, so a send the workflow never heard back from is not called unsent", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const path = await migratedDb([{ id: 305, runAt: "2026-09-23 10:25:00" }]);
    await openDb(path).exec("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-09-23', 1, 305, ''); INSERT INTO broadcasts (date, run_id, revision, status, resend_id) VALUES ('2026-09-23', 305, 1, 'sending', 'b1')");
    const sent: Email[] = [];
    const send = (e: Email) => {
      sent.push(e);
      return Promise.resolve({ id: "em" });
    };
    await opsActivities({ dbUrl: path, env, maxAttempts: 3, send }).alert({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: 305, reason: "deadline", timedOut: true, sent: false });
    expect(sent[0]?.subject).toBe("[Alert] digest-2026-09-23 timed out after the digest was sent (run 305)");
    expect(sent[0]?.html).not.toContain("--resume");
  });
  it("healthcheck maps success to the bare ping URL and logs through /log", async () => {
    const urls: string[] = [];
    const fetch = ((url: string) => {
      urls.push(url);
      return Promise.resolve(new Response("OK"));
    }) as unknown as typeof globalThis.fetch;
    const ops = opsActivities({ dbUrl: await db(), env: { HEALTHCHECK_PING_URL: "https://hc-ping.com/u" }, maxAttempts: 3, fetch });
    await ops.healthcheck("start");
    await ops.healthcheck("success");
    await ops.healthcheck("fail");
    await ops.healthcheckLog("curation start");
    expect(urls).toEqual(["https://hc-ping.com/u/start", "https://hc-ping.com/u", "https://hc-ping.com/u/fail", "https://hc-ping.com/u/log"]);
  });
});
