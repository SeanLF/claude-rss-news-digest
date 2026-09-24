import { afterEach, describe, expect, it, vi } from "vitest";
import type { Email, SendEmail } from "../mail/resend.js";
import { alertEmail, sendAlert, type AlertRequest } from "./alerts.js";

const env = { HEALTH_ALERT_EMAIL: "ops@example.com", RESEND_API_KEY: "re_test", RESEND_FROM: "digest@example.com" };
function recorder(fail?: Error) {
  const sent: [Email, { idempotencyKey?: string } | undefined][] = [];
  const send: SendEmail = (email, opts) => {
    sent.push([email, opts]);
    return fail ? Promise.reject(fail) : Promise.resolve({ id: "em_1" });
  };
  return { sent, send };
}
const runHealth: AlertRequest = { kind: "run-health", runId: 305, violations: ["ZERO_STORIES: the run completed but shipped no stories"] };
afterEach(() => {
  vi.restoreAllMocks();
});

describe("alertEmail", () => {
  it("run-health lists every violation under the run's number", () => {
    const e = alertEmail({ kind: "run-health", runId: 305, violations: ["A: one", "B: two"] });
    expect(e.subject).toBe("[Alert] Run 305 completed but violated 2 invariant(s)");
    expect(e.html).toContain("  • A: one\n  • B: two");
    expect(e.dropped).toBe("run 305 violated: A: one; B: two");
  });
  it("source-health names each failing feed and the threshold it crossed", () => {
    const e = alertEmail({ kind: "source-health", failing: [["the_hindu", 5], ["france24", 3]], failedThisRun: 4, totalSources: 38, threshold: 3 });
    expect(e.subject).toBe("[Alert] 2 RSS sources failing");
    expect(e.html).toContain("<strong>4/38</strong> sources failed this run");
    expect(e.html).toContain("failed 3+ times in a row");
    expect(e.html).toContain("  • the_hindu: 5 consecutive failures");
    expect(e.dropped).toBe("4/38 sources failed this run; persistently failing: the_hindu (5x), france24 (3x)");
  });
  it("archival names the steps and the run", () => {
    const e = alertEmail({ kind: "archival", runId: 305, failed: ["thread_links", "thread_assignments"] });
    expect(e.subject).toBe("[Alert] Digest archival failed (thread_links, thread_assignments)");
    expect(e.dropped).toBe("archival failed for thread_links, thread_assignments on run 305");
  });
  it("run-failed says whether the run timed out and escapes the error text", () => {
    const e = alertEmail({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: 305, reason: "Activity task failed: <select>", timedOut: false, sent: false });
    expect(e.subject).toBe("[Alert] digest-2026-09-23 failed (run 305)");
    expect(e.html).toContain("Activity task failed: &lt;select&gt;");
    expect(e.html).toContain("was not sent");
    expect(e.html).toContain("--resume 305");
    const t = alertEmail({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: null, reason: "deadline", timedOut: true, sent: false });
    expect(t.subject).toBe("[Alert] digest-2026-09-23 timed out (run not started)");
  });
  it.each([
    [{ sent: true }],
    [{ sent: false, broadcastStatus: "queued", date: "2026-09-23" }],
  ])("run-failed after an accepted broadcast says it was sent and never suggests a resume (%o)", (over) => {
    const e = alertEmail({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: 305, reason: "SQLITE_BUSY", timedOut: false, ...over });
    expect(e.subject).toBe("[Alert] digest-2026-09-23 failed after the digest was sent (run 305)");
    expect(e.html).toContain("The digest was sent");
    expect(e.html).not.toContain("--resume");
    expect(e.html).not.toContain("not sent");
  });
  it("run-failed with a send claim held says to check Resend and names the command that clears it", () => {
    const e = alertEmail({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: 305, reason: "claimed", timedOut: false, sent: false, broadcastStatus: "claimed 2026-09-23T12:00:00.000Z abc", date: "2026-09-23" });
    expect(e.subject).toBe("[Alert] digest-2026-09-23 failed with a send claim held (run 305)");
    expect(e.html).toContain("Check Resend");
    expect(e.html).toContain("node dist/cli/clear-claim.js 2026-09-23");
  });
  it("run-failed with a draft created but its send unconfirmed says delivery is unknown, and to check Resend before any resume", () => {
    const e = alertEmail({ kind: "run-failed", workflowId: "digest-2026-09-23", runId: 305, reason: "send timed out", timedOut: false, sent: false, broadcastStatus: "created", date: "2026-09-23" });
    expect(e.subject).toBe("[Alert] digest-2026-09-23 failed with delivery unknown (run 305)");
    expect(e.html).toContain("Check Resend");
    expect(e.html).not.toContain("not sent");
  });
  it("not-sent names why the day was not delivered and what to do", () => {
    const d = alertEmail({ kind: "not-sent", workflowId: "digest-2026-09-23", runId: 305, reason: "disabled", detail: "broadcasting disabled on this worker" });
    expect(d.subject).toBe("[Alert] Digest not sent: broadcasting disabled on this worker (run 305)");
    const h = alertEmail({ kind: "not-sent", workflowId: "digest-2026-09-23", runId: 305, reason: "held-out", detail: "the run reached the send 3 minute(s) late" });
    expect(h.subject).toBe("[Alert] Digest not sent: no time left for the send (run 305)");
    expect(h.html).toContain("resume it on a fresh budget");
    expect(h.html).toContain('ARGS="--resume 305"');
  });
});

describe("sendAlert", () => {
  it("sends to the operator address from the alerts sender, with the idempotency key", async () => {
    const { sent, send } = recorder();
    expect(await sendAlert(runHealth, { env, send, attempt: 1, maxAttempts: 3, idempotencyKey: "wf/run/1" })).toBe("sent");
    expect(sent).toHaveLength(1);
    expect(sent[0]![0]).toMatchObject({ from: "News Digest Alerts <digest@example.com>", to: ["ops@example.com"], subject: "[Alert] Run 305 completed but violated 1 invariant(s)" });
    expect(sent[0]![1]).toEqual({ idempotencyKey: "wf/run/1" });
  });
  it("with alerting unconfigured, logs at error what the alert said and sends nothing", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { sent, send } = recorder();
    expect(await sendAlert(runHealth, { env: { RESEND_API_KEY: "re_test" }, send, attempt: 1, maxAttempts: 3 })).toBe("dropped");
    expect(sent).toHaveLength(0);
    expect(err).toHaveBeenCalledWith("ALERTING MISCONFIGURED (HEALTH_ALERT_EMAIL/RESEND_FROM unset): run-health alert DROPPED, not delivered. It said: run 305 violated: ZERO_STORIES: the run completed but shipped no stories");
  });
  it("a failed send throws while attempts remain, so the activity's retry policy tries again", async () => {
    const { send } = recorder(new Error("503"));
    await expect(sendAlert(runHealth, { env, send, attempt: 1, maxAttempts: 3 })).rejects.toThrow("503");
  });
  it("a failed send on the last attempt is logged with its content and reported dropped, never thrown", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { send } = recorder(new Error("503"));
    expect(await sendAlert(runHealth, { env, send, attempt: 3, maxAttempts: 3 })).toBe("dropped");
    expect(err).toHaveBeenCalledWith("run-health alert send FAILED (Error: 503); alert DROPPED. It said: run 305 violated: ZERO_STORIES: the run completed but shipped no stories");
  });
});
