import { WorkflowFailedError } from "@temporalio/client";
import { ApplicationFailure, WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Activities, AlertRequest, FulltextFetch, FulltextTask, GnewsDecode, ThreadsReport } from "../activities/index.js";
import type { Pointer } from "../store/artifacts.js";
import { stubActivities } from "../activities/stub.js";
import { DEADLINE_MARGIN_MS, DigestWorkflow, HOLD_TIMEOUT, NOTIFY_MS, TAIL_MARGIN_MS, TAIL_WORST_CASE_MS, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./digest.workflow.js";
import { PYTHON_TASK_QUEUE } from "./policy.js";
import { approveSignal, retrySignal } from "./signals.js";

let env: TestWorkflowEnvironment;
beforeAll(async () => {
  env = await TestWorkflowEnvironment.createTimeSkipping();
}, 180_000);
afterAll(async () => {
  await env?.teardown();
});

const taskQueue = "digest-test";
// `fetcher` stands in for the Python worker on its own queue; null means nothing answers
// there, as when that worker is down. `decoder` is the decode, on the workflow's own worker.
const emptyFetch = (tasks: FulltextTask[]): Promise<FulltextFetch> => Promise.resolve({ tasks: tasks.length, results: {}, outcome: "completed" });
const noDecode = (urls: string[]): Promise<GnewsDecode> => Promise.resolve({ links: urls.length, decoded: {}, attempted: urls.length, outcome: "completed" });
const noLinks: Activities["planGnews"] = () => Promise.resolve({ urls: [], skip: "no_candidates" as const });
async function withWorker<T>(fn: () => Promise<T>, overrides: Partial<Activities> = {}, fetcher: ((tasks: FulltextTask[]) => Promise<FulltextFetch>) | null = emptyFetch, decoder: (urls: string[]) => Promise<GnewsDecode> = noDecode): Promise<T> {
  const worker = await Worker.create({
    connection: env.nativeConnection,
    taskQueue,
    workflowsPath: new URL("./digest.workflow.ts", import.meta.url).pathname,
    activities: { ...stubActivities(), decodeLinks: decoder, ...overrides },
  });
  if (!fetcher) return worker.runUntil(fn());
  const python = await Worker.create({ connection: env.nativeConnection, taskQueue: PYTHON_TASK_QUEUE, activities: { fetchFulltext: fetcher } });
  return python.runUntil(worker.runUntil(fn()));
}
function retracting() {
  const retracted: number[] = [];
  return { retracted, acts: { threadsRetract: (runId: number) => { retracted.push(runId); return Promise.resolve({ retracted: true }); } } as Partial<Activities> };
}
// A clean run sends without a signal: none is needed, and one sent after it ends would fail.
const runToEnd = async (runDate: string) => (await start(runDate)).result();
const start = (runDate: string, extra: Record<string, unknown> = {}, opts: Record<string, unknown> = {}) =>
  env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor(runDate), args: [{ runDate, ...extra }], ...opts });

const nonRetryable = (msg: string) => Promise.reject(ApplicationFailure.nonRetryable(msg, "TestFailure"));
function spy(overrides: Partial<Activities>) {
  const seen: { reports: ThreadsReport[]; rendered: Pointer[] } = { reports: [], rendered: [] };
  const acts: Partial<Activities> = {
    threadsFinish: (runId, report) => {
      seen.reports.push(report);
      return stubActivities().threadsFinish(runId, report);
    },
    render: (runId, selections, threads, gnews) => {
      seen.rendered.push(threads);
      return stubActivities().render(runId, selections, threads, gnews);
    },
    ...overrides,
  };
  return { seen, acts };
}

// Records the operations calls in order, with SELECT among them to check what runs before it.
function recorder(over: Partial<Activities> = {}) {
  const calls: unknown[][] = [];
  const stubs = stubActivities() as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>;
  const track = <K extends keyof Activities>(name: K, keep: (a: unknown[]) => unknown[] = (a) => a): Activities[K] =>
    ((...a: unknown[]) => {
      calls.push([name, ...keep(a)]);
      return stubs[name]!(...a);
    }) as unknown as Activities[K];
  const acts: Partial<Activities> = {
    healthcheck: track("healthcheck"),
    checkFeeds: track("checkFeeds", ([runId, ids]) => [runId, (ids as string[]).length]),
    weeklyRecap: track("weeklyRecap", ([runId]) => [runId]),
    select: track("select", () => []),
    checkRunHealth: track("checkRunHealth"),
    alert: track("alert"),
    ...over,
  };
  return { calls, acts };
}
const named = (calls: unknown[][], name: string) => calls.filter((c) => c[0] === name);
const failed = (reason: string) => () => Promise.reject(ApplicationFailure.nonRetryable(reason, "TestFailure"));
// Every tail activity logs its name; the stubs answer.
function tail(overrides: Partial<Activities> = {}) {
  const calls: string[] = [];
  const stub = stubActivities();
  const logged = (name: keyof Activities): Partial<Activities> => ({
    [name]: (...args: unknown[]) => {
      calls.push(name);
      const impl = (overrides[name] ?? stub[name]) as (...a: unknown[]) => Promise<unknown>;
      return impl(...args);
    },
  });
  const names = ["archiveRun", "render", "checkPreSend", "sendEnabled", "notifyHold", "saveDigest", "broadcast", "recordShownHeadlines", "finishRun", "abortRun"] as const;
  return { calls, acts: Object.assign({}, ...names.map((n) => logged(n))) as Partial<Activities> };
}
// A run that fails its pre-send checks, and what its hold notification was told.
const FAILED = ["INTERNAL_ID_LEAK: 1 leak(s): must_know.summary '(A2)' in 'Deal signed'", "THREAD_AUDIT_FAILED: 1 thread update(s) shipped facts their audit could not check (it fails open)"];
function flagged(failures: string[] = FAILED) {
  const notices: { holdEndsAt: string; failures: string[]; at: number }[] = [];
  const acts: Partial<Activities> = {
    checkPreSend: () => Promise.resolve(failures),
    notifyHold: (_r, _s, holdEndsAt, f) => {
      notices.push({ holdEndsAt, failures: f, at: Date.now() });
      return Promise.resolve({ sent: true });
    },
  };
  return { notices, acts };
}
// A send that records itself.
const sending = (sends: string[]): Activities["broadcast"] => () => {
  sends.push("sent");
  return Promise.resolve({ broadcastId: "b", status: "sent", recipients: 3 });
};
// The timers the run started after the render: the hold's, when there is one.
async function timersAfterRender(h: { fetchHistory: () => Promise<{ events?: { eventType?: unknown; activityTaskScheduledEventAttributes?: { activityType?: { name?: string | null } | null } | null; timerStartedEventAttributes?: { startToFireTimeout?: { seconds?: unknown } | null } | null }[] | null }> }): Promise<number[]> {
  const events = (await h.fetchHistory()).events ?? [];
  const rendered = events.findIndex((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === "render");
  return events.slice(rendered).flatMap((e) => (e.timerStartedEventAttributes ? [Number(e.timerStartedEventAttributes.startToFireTimeout?.seconds ?? 0)] : []));
}
// Until the flagged run's hold notification went out (the hold's timer follows it at once).
async function untilNotified(notices: unknown[]): Promise<void> {
  while (!notices.length) await new Promise((r) => setTimeout(r, 20));
}

// SELECT fails once and parks until the operator retries it. Nobody approves the hold.
function parkedOnce(over: Partial<Activities> = {}) {
  let n = 0;
  const select: Activities["select"] = (runId, ...rest) => (n++ === 0 ? Promise.reject(ApplicationFailure.nonRetryable("transient", "T")) : stubActivities().select(runId, ...rest));
  return recorder({ select, ...over });
}
describe("DigestWorkflow", () => {
  it("runs every stage over stub activities and sends", async () => {
    const out = await withWorker(() => runToEnd("2026-09-21"));
    expect(out).toMatchObject({ runId: 1, broadcast: "sent", stories: 3 });
  }, 120_000);
  it("a clean run sends at once: no hold, no hold notice, no timer", async () => {
    const { calls, acts } = tail();
    const out = await withWorker(async () => {
      const h = await start("2026-09-22");
      const result = await h.result();
      return { result, timers: await timersAfterRender(h) };
    }, acts);
    expect(out.result.broadcast).toBe("sent");
    expect(calls).not.toContain("notifyHold");
    expect(out.timers).toEqual([]);
  }, 120_000);
  it("a run that fails a check holds 15 minutes, then sends anyway", async () => {
    const { notices, acts } = flagged();
    const out = await withWorker(async () => {
      const h = await start("2026-09-20");
      const result = await h.result(); // no signal: time-skipping runs the hold out
      return { result, timers: await timersAfterRender(h) };
    }, acts);
    expect(out.result.broadcast).toBe("sent");
    expect(notices).toHaveLength(1);
    expect(out.timers).toHaveLength(1);
    expect(out.timers[0]).toBeGreaterThan(14 * 60);
    expect(out.timers[0]).toBeLessThanOrEqual(15 * 60);
    expect(HOLD_TIMEOUT).toBe(15 * 60 * 1000);
  }, 120_000);
  it("the hold notice lists every failed check", async () => {
    const { notices, acts } = flagged();
    await withWorker(async () => (await start("2026-09-19")).result(), acts);
    expect(notices.map((n) => n.failures)).toEqual([FAILED]);
  }, 120_000);
  it("an approve during the hold sends now", async () => {
    const { notices, acts } = flagged();
    const sends: string[] = [];
    const out = await withWorker(async () => {
      const h = await start("2026-09-18");
      await untilNotified(notices);
      await h.signal(approveSignal, { decision: "approve" });
      const result = await h.result();
      const events = (await h.fetchHistory()).events ?? [];
      return { broadcast: result.broadcast, fired: events.filter((e) => e.timerFiredEventAttributes).length };
    }, { ...acts, broadcast: sending(sends) });
    expect(out.broadcast).toBe("sent");
    expect(out.fired).toBe(0); // the approve ended the hold, not its timer
    expect(sends).toEqual(["sent"]);
  }, 120_000);
  it("a reject during the hold does not send", async () => {
    const { notices, acts } = flagged();
    const sends: string[] = [];
    const out = await withWorker(async () => {
      const h = await start("2026-09-17");
      await untilNotified(notices);
      await h.signal(approveSignal, { decision: "reject" });
      return h.result();
    }, { ...acts, broadcast: sending(sends) });
    expect(out.broadcast).toBe("rejected");
    expect(sends).toEqual([]);
  }, 120_000);
  it("checks that cannot run are a failed check: the run holds and says why", async () => {
    const { notices, acts } = flagged();
    const out = await withWorker(async () => (await start("2026-09-16")).result(), { ...acts, checkPreSend: () => Promise.reject(ApplicationFailure.nonRetryable("database gone", "T")) });
    expect(out.broadcast).toBe("sent");
    expect(notices.map((n) => n.failures)).toEqual([[expect.stringMatching(/^PRE_SEND_CHECK_ERROR: the checks could not run: .*database gone/)]]);
  }, 120_000);
  it("a reject that lands before the send stops a clean run", async () => {
    let release!: () => void;
    const delivered = new Promise<void>((r) => (release = r));
    const out = await withWorker(async () => {
      const h = await start("2026-09-26");
      await h.signal(approveSignal, { decision: "reject" });
      release();
      return h.result();
    }, {
      // The checks end only once the reject is in.
      checkPreSend: async () => {
        await delivered;
        return [];
      },
    });
    expect(out.broadcast).toBe("rejected");
  }, 120_000);
  it("rejects a second start for the same day while one is running", async () => {
    await withWorker(async () => {
      const h = await start("2026-09-23", {}, { workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL });
      await expect(start("2026-09-23", {}, { workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL })).rejects.toThrow();
      await h.signal(approveSignal, { decision: "approve" });
      await h.result();
    }, flagged().acts); // held, so the first is still running at the second start
  }, 120_000);
  it("parks on the retry signal when select fails non-retryably, and aborts on 'abort'", async () => {
    const out = await withWorker(async () => {
      const h = await start("2026-09-24", { failStage: "select" });
      await h.signal(retrySignal, { decision: "abort" });
      return h.result();
    });
    expect(out).toMatchObject({ broadcast: "skipped", stories: 0 });
  }, 120_000);
  it("a retry decision sent before the failure is not lost", async () => {
    const out = await withWorker(async () => {
      const h = await start("2026-09-25", { failStage: "select" });
      await h.signal(retrySignal, { decision: "abort" }); // may arrive before select runs
      return h.result();
    });
    expect(out.broadcast).toBe("skipped");
  }, 120_000);
  it("a resume continues the named run without forcing its artifacts", async () => {
    const out = await withWorker(async () => (await start("2026-09-27", { resumeRun: 303 })).result());
    expect(out).toMatchObject({ runId: 303, broadcast: "sent" });
  }, 120_000);
  describe("operations", () => {
    it("a delivered run pings start and success, keeps the weekly recap before SELECT, and checks its health", async () => {
      const { calls, acts } = recorder();
      const out = await withWorker(() => runToEnd("2026-11-02"), acts);
      expect(out.broadcast).toBe("sent");
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "success"]]);
      expect(named(calls, "checkFeeds")).toEqual([["checkFeeds", 1, 3]]);
      expect(calls.findIndex((c) => c[0] === "weeklyRecap")).toBeLessThan(calls.findIndex((c) => c[0] === "select"));
      expect(named(calls, "checkRunHealth")).toEqual([["checkRunHealth", 1, true]]);
      expect(named(calls, "alert")).toEqual([]);
    }, 120_000);
    it("alerts on what the feed and run-health checks find", async () => {
      const feeds = { kind: "source-health" as const, failing: [["the_hindu", 4]] as [string, number][], failedThisRun: 1, totalSources: 3, threshold: 3 };
      const health = { kind: "run-health" as const, runId: 1, violations: ["ZERO_STORIES: x"] };
      const { calls, acts } = recorder({ checkFeeds: () => Promise.resolve(feeds), checkRunHealth: () => Promise.resolve(health) });
      await withWorker(() => runToEnd("2026-11-03"), acts);
      expect(named(calls, "alert")).toEqual([["alert", feeds], ["alert", health]]);
    }, 120_000);
    it("an operator's reject closes the day's /start with a note, and alerts nothing", async () => {
      const { calls, acts } = recorder(flagged().acts);
      await withWorker(async () => {
        const h = await start("2026-11-04");
        await h.signal(approveSignal, { decision: "reject" });
        return h.result();
      }, acts);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "success", "not sent: rejected by the operator"]]);
      expect(named(calls, "checkRunHealth")).toEqual([]);
      expect(named(calls, "alert")).toEqual([]);
    }, 120_000);
    it("with broadcasting disabled the run alerts that nothing was sent, and pings no success", async () => {
      const { calls, acts } = recorder({ sendEnabled: () => Promise.resolve(false) });
      const out = await withWorker(async () => (await start("2026-11-10")).result(), acts);
      expect(out.broadcast).toBe("disabled");
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"]]);
      expect(named(calls, "alert")).toMatchObject([["alert", { kind: "not-sent", reason: "disabled", detail: "broadcasting disabled on this worker" }]]);
    }, 120_000);
    it("the operations checks are best-effort: every one failing still delivers the run", async () => {
      const boom = failed("ops down");
      const out = await withWorker(() => runToEnd("2026-11-05"), { healthcheck: boom, checkFeeds: boom, weeklyRecap: boom, checkRunHealth: boom, alert: boom, healthcheckLog: boom });
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("a failed run pings fail and alerts with the cause, then still fails", async () => {
      const { calls, acts } = recorder({ writeStory: failed("write s00: the model is gone") });
      const err = await withWorker(async () => (await start("2026-11-06")).result().catch((e: unknown) => e), acts);
      expect(err).toBeInstanceOf(WorkflowFailedError);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "fail"]]);
      const [[, req]] = named(calls, "alert") as [[string, AlertRequest]];
      expect(req).toMatchObject({ kind: "run-failed", workflowId: workflowIdFor("2026-11-06"), runId: 1, timedOut: false });
      expect((req as Extract<AlertRequest, { kind: "run-failed" }>).reason).toContain("write s00: the model is gone");
    }, 120_000);
    it("a run that hangs fails loudly at its own deadline, before the server's run timeout can kill it silently", async () => {
      const { calls, acts } = recorder();
      // Parked on the retry signal with nobody answering: only the deadline ends it.
      const err = await withWorker(async () => (await start("2026-11-07", { failStage: "select" }, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT })).result().catch((e: unknown) => e), acts);
      expect(err).toBeInstanceOf(WorkflowFailedError);
      expect((err as WorkflowFailedError).cause?.message).toMatch(/deadline/);
      expect(named(calls, "alert")).toMatchObject([["alert", { kind: "run-failed", runId: 1, timedOut: true }]]);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "fail"]]);
    }, 120_000);
    it("a failed resume alerts but leaves the dead-man's switch to the day's first run", async () => {
      const { calls, acts } = recorder({ writeStory: failed("still gone") });
      await withWorker(async () => (await start("2026-11-08", { resumeRun: 303 })).result().catch((e: unknown) => e), acts);
      expect(named(calls, "healthcheck")).toEqual([]);
      expect(named(calls, "alert")).toMatchObject([["alert", { kind: "run-failed", runId: 303 }]]);
    }, 120_000);
    it("an operator's cancellation is not a failure to alert on", async () => {
      const { calls, acts } = recorder();
      await withWorker(async () => {
        const h = await start("2026-11-09");
        await env.sleep("1 minute");
        await h.cancel();
        return h.result().catch((e: unknown) => e);
      }, acts);
      expect(named(calls, "alert")).toEqual([]);
    }, 120_000);
  });
  describe("fulltext across the language line", () => {
    const stored: FulltextFetch[] = [];
    const storeFulltext: Activities["storeFulltext"] = (runId, fetched) => {
      stored.push(fetched);
      return stubActivities().storeFulltext(runId, fetched);
    };
    it("hands the planned tasks to the fulltext queue and stores what comes back", async () => {
      stored.length = 0;
      const seen: FulltextTask[][] = [];
      const out = await withWorker(() => runToEnd("2026-09-28"), { storeFulltext }, (tasks) => {
        seen.push(tasks);
        return Promise.resolve({ tasks: tasks.length, results: { A1: "Body." }, outcome: "completed" });
      });
      expect(seen).toEqual([[["A1", "https://example.com/a1"]]]);
      expect(stored).toEqual([{ tasks: 1, results: { A1: "Body." }, outcome: "completed" }]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("goes on without full text when nothing answers on the python queue", async () => {
      stored.length = 0;
      // The test server does not skip time while an activity task sits unclaimed, so the clock is
      // moved past the schedule-to-start timeout by hand. The decode is skipped here so the fetch is
      // the one unclaimed task.
      const out = await withWorker(async () => {
        const h = await start("2026-09-29");
        await env.sleep("6 minutes");
        return h.result();
      }, { storeFulltext, planGnews: noLinks }, null);
      expect(stored).toEqual([{ tasks: 1, results: {}, outcome: "unavailable" }]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("records a skipped fetch without calling Python", async () => {
      stored.length = 0;
      let fetched = 0;
      await withWorker(() => runToEnd("2026-10-01"), { storeFulltext, planFulltext: () => Promise.resolve({ tasks: [], skip: "disabled" as const }) }, () => {
        fetched++;
        return Promise.resolve({ tasks: 0, results: {}, outcome: "completed" });
      });
      expect(fetched).toBe(0);
      expect(stored).toEqual([{ tasks: 0, results: {}, outcome: "disabled" }]);
    }, 120_000);
    it("skips the fetch when the run already has its full text", async () => {
      stored.length = 0;
      let fetched = 0;
      const existing = { runId: 1, name: "article_fulltext.json", sha256: "1".repeat(64) };
      await withWorker(() => runToEnd("2026-09-30"), { storeFulltext, planFulltext: () => Promise.resolve({ tasks: [], existing }) }, () => {
        fetched++;
        return Promise.resolve({ tasks: 0, results: {}, outcome: "completed" });
      });
      expect(fetched).toBe(0);
      expect(stored).toEqual([]);
    }, 120_000);
  });
  describe("gnews", () => {
    const GN = "https://news.google.com/rss/articles/X";
    const stored: GnewsDecode[] = [];
    const rendered: string[] = [];
    const storeGnews: Activities["storeGnews"] = (runId, decoded) => {
      stored.push(decoded);
      return stubActivities().storeGnews(runId, decoded);
    };
    const render: Activities["render"] = (runId, selections, threads, gnews) => {
      rendered.push(`${gnews.name}@${gnews.sha256.slice(0, 4)}`); // the stub store hashes to 0000
      return stubActivities().render(runId, selections, threads, gnews);
    };
    const reset = () => {
      stored.length = 0;
      rendered.length = 0;
    };
    it("hands the surviving links to the decoder, stores what comes back, and renders with it", async () => {
      reset();
      const seen: string[][] = [];
      const out = await withWorker(() => runToEnd("2026-10-02"), { storeGnews, render }, emptyFetch, (urls) => {
        seen.push(urls);
        return Promise.resolve({ links: urls.length, decoded: { [GN]: "https://www.reuters.com/x" }, attempted: 1, outcome: "completed" });
      });
      expect(seen).toEqual([[GN]]);
      expect(stored).toEqual([{ links: 1, decoded: { [GN]: "https://www.reuters.com/x" }, attempted: 1, outcome: "completed" }]);
      expect(rendered).toEqual(["gnews_links.json@0000"]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("a decode that fails after it started ships the raw links: stored as failed, never retried, and the run goes on", async () => {
      reset();
      let calls = 0;
      const out = await withWorker(() => runToEnd("2026-10-03"), { storeGnews }, emptyFetch, () => {
        calls++;
        return Promise.reject(new Error("decoder blew up"));
      });
      expect(calls).toBe(1);
      expect(stored).toEqual([{ links: 1, decoded: {}, attempted: 0, outcome: "failed" }]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("records a skipped decode without calling the decoder", async () => {
      reset();
      let calls = 0;
      await withWorker(() => runToEnd("2026-10-04"), { storeGnews, planGnews: () => Promise.resolve({ urls: [], skip: "no_candidates" as const }) }, emptyFetch, (urls) => {
        calls++;
        return noDecode(urls);
      });
      expect(calls).toBe(0);
      expect(stored).toEqual([{ links: 0, decoded: {}, attempted: 0, outcome: "no_candidates" }]);
    }, 120_000);
    it("renders with the links a resumed run already decoded, spending no requests", async () => {
      reset();
      let calls = 0;
      const existing = { runId: 1, name: "gnews_links.json", sha256: "1".repeat(64) };
      await withWorker(() => runToEnd("2026-10-05"), { storeGnews, render, planGnews: () => Promise.resolve({ urls: [], existing }) }, emptyFetch, (urls) => {
        calls++;
        return noDecode(urls);
      });
      expect(calls).toBe(0);
      expect(stored).toEqual([]);
      expect(rendered).toEqual(["gnews_links.json@1111"]);
    }, 120_000);
  });
  describe("threads are best-effort", () => {
    it("a linker that fails ships the digest and records why", async () => {
      const { seen, acts } = spy({ threadsLink: () => nonRetryable("linker down") });
      const out = await withWorker(() => runToEnd("2026-10-02"), acts);
      expect(out.broadcast).toBe("sent");
      expect(seen.reports).toEqual([{ outcomes: [], failures: [], linkError: "linker down" }]);
      expect(seen.rendered.map((p) => p.name)).toEqual(["thread_context.json"]);
    }, 120_000);
    it("one synthesis that fails is recorded and the rest still land", async () => {
      const plans = [{ threadId: 7, articleIds: ["A1", "A2"] }, { threadId: 8, articleIds: ["A3", "A4"] }];
      const { seen, acts } = spy({
        threadsLink: () => Promise.resolve({ plans }),
        threadSynthesis: (_runId, plan) => (plan.threadId === 7 ? nonRetryable("synthesis broke") : Promise.resolve({ threadId: 8, auditFailed: true })),
      });
      const out = await withWorker(() => runToEnd("2026-10-03"), acts);
      expect(out.broadcast).toBe("sent");
      expect(seen.reports).toEqual([{ outcomes: [{ threadId: 8, auditFailed: true }], failures: [{ threadId: 7, error: "synthesis broke" }] }]);
    }, 120_000);
    it("passes a forced start's force to the link", async () => {
      const forced: (boolean | undefined)[] = [];
      const { acts } = spy({
        threadsLink: (_runId, force) => {
          forced.push(force);
          return Promise.resolve({ plans: [] });
        },
      });
      await withWorker(async () => (await start("2026-10-06", { force: true })).result(), acts);
      expect(forced).toEqual([true]);
    }, 120_000);
    it("a finish that fails leaves the render a pointer to no context, and the digest ships", async () => {
      const { seen, acts } = spy({ threadsFinish: () => nonRetryable("db locked") });
      const out = await withWorker(() => runToEnd("2026-10-04"), acts);
      expect(out.broadcast).toBe("sent");
      expect(seen.rendered).toEqual([{ runId: 1, name: "thread_context.json", sha256: "0".repeat(64) }]);
    }, 120_000);
  });
  describe("an issue that is not sent takes back its thread writes", () => {
    it("a rejected issue retracts", async () => {
      const { retracted, acts } = retracting();
      const out = await withWorker(async () => {
        const h = await start("2026-11-30");
        await h.signal(approveSignal, { decision: "reject" });
        return h.result();
      }, { ...flagged().acts, ...acts });
      expect(out.broadcast).toBe("rejected");
      expect(retracted).toEqual([1]);
    }, 120_000);
    it("a sent issue keeps them", async () => {
      const { retracted, acts } = retracting();
      const out = await withWorker(() => runToEnd("2026-12-01"), acts);
      expect(out.broadcast).toBe("sent");
      expect(retracted).toEqual([]);
    }, 120_000);
  });
  describe("the tail: record, hold, send", () => {
    it("a clean run: archives and checks before the send, and publishes, sends and records the shown headlines", async () => {
      const { calls, acts } = tail();
      const out = await withWorker(async () => (await start("2026-10-02")).result(), acts);
      expect(calls).toEqual(["archiveRun", "render", "checkPreSend", "sendEnabled", "saveDigest", "broadcast", "recordShownHeadlines", "finishRun"]);
      expect(out).toMatchObject({ broadcast: "sent", recipients: 12 });
    }, 120_000);
    it("a flagged run: publishes, sends and records the shown headlines only after the hold", async () => {
      const f = flagged();
      const { calls, acts } = tail(f.acts);
      const out = await withWorker(async () => (await start("2026-10-12")).result(), acts);
      expect(calls).toEqual(["archiveRun", "render", "checkPreSend", "sendEnabled", "notifyHold", "saveDigest", "broadcast", "recordShownHeadlines", "finishRun"]);
      expect(out).toMatchObject({ broadcast: "sent", recipients: 12 });
    }, 120_000);
    it("a rejected issue is neither published, sent nor recorded as shown", async () => {
      const { calls, acts } = tail(flagged().acts);
      const out = await withWorker(async () => {
        const h = await start("2026-10-03");
        await h.signal(approveSignal, { decision: "reject" });
        return h.result();
      }, acts);
      expect(calls).toEqual(["archiveRun", "render", "checkPreSend", "sendEnabled", "finishRun"]); // rejected before the hold: no hold notice
      expect(out.broadcast).toBe("rejected");
    }, 120_000);
    it("a send that fails is not retried: the run is marked failed and the workflow fails", async () => {
      const { calls, acts } = tail({ broadcast: () => Promise.reject(new Error("read timeout")) });
      await expect(withWorker(() => runToEnd("2026-10-04"), acts)).rejects.toThrow();
      expect(calls.filter((c) => c === "broadcast")).toHaveLength(1);
      expect(calls.slice(-2)).toEqual(["broadcast", "abortRun"]);
    }, 120_000);
    it("a hold notification that fails does not stop the send at the hold's end", async () => {
      const { calls, acts } = tail({ checkPreSend: () => Promise.resolve(FAILED), notifyHold: () => Promise.reject(ApplicationFailure.nonRetryable("resend down")) });
      const out = await withWorker(async () => (await start("2026-10-05")).result(), acts);
      expect(out.broadcast).toBe("sent");
      expect(calls).toContain("broadcast");
    }, 120_000);
    it("with the send disabled nothing is published, sent, held for or recorded as shown, and the alert carries the failed checks, not the cut-over hold", async () => {
      const ops = recorder();
      const CUTOVER = "CUTOVER_HOLD: every run through 2026-10-06 holds for the cut-over (HOLD_ALWAYS_THROUGH); no check failed";
      const { calls, acts } = tail({ sendEnabled: () => Promise.resolve(false), checkPreSend: () => Promise.resolve([CUTOVER, ...FAILED]) });
      const out = await withWorker(async () => (await start("2026-10-06")).result(), { ...ops.acts, ...acts });
      expect(calls).toEqual(["archiveRun", "render", "checkPreSend", "sendEnabled", "finishRun"]);
      expect(out).toMatchObject({ broadcast: "disabled" });
      expect(named(ops.calls, "alert")).toMatchObject([["alert", { kind: "not-sent", reason: "disabled", detail: `broadcasting disabled on this worker; the pre-send checks failed: ${FAILED.join("; ")}` }]]);
    }, 120_000);
    it("a failure recording after a delivered send is retried, not a failed run", async () => {
      let tries = 0;
      const { calls, acts } = tail({
        recordShownHeadlines: () => (++tries === 1 ? Promise.reject(new Error("SQLITE_BUSY: database is locked")) : Promise.resolve({ rows: 3 })),
        finishRun: () => (tries++ === 2 ? Promise.reject(new Error("SQLITE_BUSY: database is locked")) : Promise.resolve()),
      });
      const out = await withWorker(() => runToEnd("2026-10-07"), acts);
      expect(out).toMatchObject({ broadcast: "sent" });
      expect(calls.filter((c) => c === "broadcast")).toHaveLength(1);
      expect(calls).not.toContain("abortRun");
    }, 120_000);
    it("a flagged run's hold is cut to what the deadline leaves after the send, still sends, and the notice says when it ends", async () => {
      const f = flagged();
      const out = await withWorker(async () => {
        // A 50-minute run timeout: a 40-minute deadline, 10 minutes before the tail's margin.
        const h = await start("2026-10-08", {}, { workflowRunTimeout: "50 minutes" });
        const { startTime } = await h.describe();
        const result = await h.result(); // no signal: the cut hold runs out
        return { result, startTime };
      }, f.acts);
      expect(out.result.broadcast).toBe("sent");
      const held = Date.parse(f.notices[0]!.holdEndsAt) - out.startTime.getTime();
      expect(held).toBeLessThanOrEqual(50 * 60 * 1000 - DEADLINE_MARGIN_MS - TAIL_MARGIN_MS + 1000); // the server's start time and the run's can differ by a millisecond
      expect(held).toBeLessThan(HOLD_TIMEOUT);
      expect(held).toBeGreaterThan(9 * 60 * 1000);
    }, 120_000);
    it.each([
      ["clean", {}],
      ["flagged", { checkPreSend: () => Promise.resolve(FAILED) }],
    ] as [string, Partial<Activities>][])("a %s run with no budget left for the send and its record is held out: not sent, the operator told, the switch failed", async (_kind, over) => {
      const { calls, acts } = tail(over);
      const ops = recorder();
      const out = await withWorker(async () => (await start("2026-10-09", {}, { workflowRunTimeout: "20 minutes" })).result(), { ...ops.acts, ...acts });
      expect(out.broadcast).toBe("held-out");
      expect(calls).not.toContain("notifyHold");
      expect(calls).not.toContain("broadcast");
      expect(named(ops.calls, "alert")).toMatchObject([["alert", { kind: "not-sent", reason: "held-out" }]]);
      expect(named(ops.calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "fail"]]);
    }, 120_000);
    describe("one budget: the deadline never cuts a send, and the hold is cut to it", () => {
      it("the tail's worst case, every step timing out on every attempt, fits its margin", () => {
        expect(TAIL_WORST_CASE_MS).toBeLessThanOrEqual(TAIL_MARGIN_MS);
        // A flagged run's hold notice can start with under a minute of budget left and overrun it.
        expect(TAIL_WORST_CASE_MS + NOTIFY_MS).toBeLessThanOrEqual(TAIL_MARGIN_MS);
      });
      it("an early approval does not skip the budget: approved, then parked to 229 minutes, the run is held out", async () => {
        const sends: string[] = [];
        const { calls, acts } = parkedOnce({
          broadcast: () => {
            sends.push("sent");
            return Promise.resolve({ broadcastId: "b1", status: "sent", recipients: 5 });
          },
        });
        const out = await withWorker(async () => {
          const h = await start("2026-11-23", {}, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT });
          await h.signal(approveSignal, { decision: "approve" });
          await env.sleep("229 minutes");
          await h.signal(retrySignal, { decision: "retry" });
          return h.result();
        }, acts);
        expect(out.broadcast).toBe("held-out");
        expect(sends).toEqual([]);
        expect(named(calls, "alert")).toMatchObject([["alert", { kind: "not-sent", reason: "held-out" }]]);
      }, 120_000);
      it("a clean run retried at 195 minutes, with 5 of its 200 minutes before the tail left, sends", async () => {
        const { calls, acts } = parkedOnce();
        const out = await withWorker(async () => {
          const h = await start("2026-11-24", {}, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT });
          await env.sleep("195 minutes");
          await h.signal(retrySignal, { decision: "retry" });
          return h.result();
        }, acts);
        expect(out.broadcast).toBe("sent");
        expect(named(calls, "alert")).toEqual([]);
      }, 120_000);
      it("an operator's retry at 115 minutes still gets a sent digest (the reviewer's case)", async () => {
        const { calls, acts } = parkedOnce();
        const out = await withWorker(async () => {
          const h = await start("2026-11-20", {}, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT });
          await env.sleep("115 minutes");
          await h.signal(retrySignal, { decision: "retry" });
          return h.result();
        }, acts);
        expect(out.broadcast).toBe("sent");
        expect(named(calls, "alert")).toEqual([]);
        expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "success"]]);
      }, 120_000);
      it("the reviewer's mid-send case, flagged and retried at 190 minutes: the hold is cut to the deadline, so the send ends before it", async () => {
        const sends: string[] = [];
        const finished: string[] = [];
        const { calls, acts } = parkedOnce({
          checkPreSend: () => Promise.resolve(FAILED),
          broadcast: async () => {
            sends.push("sent");
            return { broadcastId: "b1", status: "sent", recipients: 12 };
          },
          finishRun: (_r, out) => {
            finished.push(out.broadcast);
            return Promise.resolve();
          },
        });
        const out = await withWorker(async () => {
          const h = await start("2026-11-21", {}, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT });
          await env.sleep("190 minutes");
          await h.signal(retrySignal, { decision: "retry" });
          return h.result();
        }, acts);
        expect(out).toMatchObject({ broadcast: "sent", recipients: 12 });
        expect(sends).toEqual(["sent"]);
        expect(finished).toEqual(["sent"]);
        expect(named(calls, "alert")).toEqual([]);
        expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "success"]]);
      }, 120_000);
      it("a cancellation that lands mid-send cancels neither the send nor its record", async () => {
        // The tail's worst case fits its margin, so the deadline cannot reach it; an operator's
        // cancellation can, and the send and its record are shielded from it the same way.
        const flags = { started: false };
        let release!: () => void;
        const gate = new Promise<void>((r) => (release = r));
        const sends: string[] = [];
        const finished: string[] = [];
        const { acts } = recorder({
          broadcast: async () => {
            flags.started = true;
            await gate;
            sends.push("sent");
            return { broadcastId: "b1", status: "sent", recipients: 12 };
          },
          finishRun: (_r, out) => {
            finished.push(out.broadcast);
            return Promise.resolve();
          },
        });
        const out = await withWorker(async () => {
          const h = await start("2026-11-22");
          await h.signal(approveSignal, { decision: "approve" });
          while (!flags.started) await new Promise((r) => setTimeout(r, 20));
          await h.cancel();
          await new Promise((r) => setTimeout(r, 500));
          release();
          return h.result();
        }, acts);
        expect(out).toMatchObject({ broadcast: "sent", recipients: 12 });
        expect(sends).toEqual(["sent"]);
        expect(finished).toEqual(["sent"]);
      }, 120_000);
    });
  });
});
