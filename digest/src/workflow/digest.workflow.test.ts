import { WorkflowFailedError } from "@temporalio/client";
import { ApplicationFailure, WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Activities, AlertRequest, FulltextFetch, FulltextTask, ThreadsReport } from "../activities/index.js";
import type { Pointer } from "../store/artifacts.js";
import { stubActivities } from "../activities/stub.js";
import { DEADLINE_MARGIN_MS, DigestWorkflow, HOLD_MINIMUM_MS, TAIL_MARGIN_MS, TAIL_WORST_CASE_MS, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./digest.workflow.js";
import { FULLTEXT_TASK_QUEUE } from "./policy.js";
import { approveSignal, retrySignal } from "./signals.js";

let env: TestWorkflowEnvironment;
beforeAll(async () => {
  env = await TestWorkflowEnvironment.createTimeSkipping();
}, 180_000);
afterAll(async () => {
  await env?.teardown();
});

const taskQueue = "digest-test";
// `fetcher` stands in for the Python fulltext worker on its own queue; null means nothing answers
// there, as when that worker is down.
const emptyFetch = (tasks: FulltextTask[]): Promise<FulltextFetch> => Promise.resolve({ tasks: tasks.length, results: {}, outcome: "completed" });
async function withWorker<T>(fn: () => Promise<T>, overrides: Partial<Activities> = {}, fetcher: ((tasks: FulltextTask[]) => Promise<FulltextFetch>) | null = emptyFetch): Promise<T> {
  const worker = await Worker.create({
    connection: env.nativeConnection,
    taskQueue,
    workflowsPath: new URL("./digest.workflow.ts", import.meta.url).pathname,
    activities: { ...stubActivities(), ...overrides },
  });
  if (!fetcher) return worker.runUntil(fn());
  const python = await Worker.create({ connection: env.nativeConnection, taskQueue: FULLTEXT_TASK_QUEUE, activities: { fetchFulltext: fetcher } });
  return python.runUntil(worker.runUntil(fn()));
}
const approveAndWait = async (runDate: string) => {
  const h = await start(runDate);
  await h.signal(approveSignal, { decision: "approve" });
  return h.result();
};
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
  const names = ["archiveRun", "render", "sendEnabled", "notifyHold", "saveDigest", "broadcast", "recordShownHeadlines", "finishRun", "abortRun"] as const;
  return { calls, acts: Object.assign({}, ...names.map((n) => logged(n))) as Partial<Activities> };
}

// SELECT fails once and parks until the operator retries it. Nobody approves the hold.
function parkedOnce(over: Partial<Activities> = {}) {
  let n = 0;
  const select: Activities["select"] = (runId, ...rest) => (n++ === 0 ? Promise.reject(ApplicationFailure.nonRetryable("transient", "T")) : stubActivities().select(runId, ...rest));
  return recorder({ select, ...over });
}
describe("DigestWorkflow", () => {
  it("runs every stage over stub activities and sends when approved", async () => {
    const out = await withWorker(async () => {
      const h = await start("2026-09-21");
      await h.signal(approveSignal, { decision: "approve" });
      return h.result();
    });
    expect(out).toMatchObject({ runId: 1, broadcast: "sent", stories: 3 });
  }, 120_000);
  it("proceeds after the hold times out with no signal", async () => {
    const out = await withWorker(async () => (await start("2026-09-22")).result()); // time-skipping: the 2 h hold elapses
    expect(out.broadcast).toBe("sent");
  }, 120_000);
  it("a rejection during the hold does not send", async () => {
    const out = await withWorker(async () => {
      const h = await start("2026-09-26");
      await h.signal(approveSignal, { decision: "reject" });
      return h.result();
    });
    expect(out.broadcast).toBe("rejected");
  }, 120_000);
  it("rejects a second start for the same day while one is running", async () => {
    await withWorker(async () => {
      const h = await start("2026-09-23", {}, { workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL });
      await expect(start("2026-09-23", {}, { workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL })).rejects.toThrow();
      await h.signal(approveSignal, { decision: "approve" });
      await h.result();
    });
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
    const out = await withWorker(async () => {
      const h = await start("2026-09-27", { resumeRun: 303 });
      await h.signal(approveSignal, { decision: "approve" });
      return h.result();
    });
    expect(out).toMatchObject({ runId: 303, broadcast: "sent" });
  }, 120_000);
  describe("operations", () => {
    it("a delivered run pings start and success, keeps the weekly recap before SELECT, and checks its health", async () => {
      const { calls, acts } = recorder();
      const out = await withWorker(() => approveAndWait("2026-11-02"), acts);
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
      await withWorker(() => approveAndWait("2026-11-03"), acts);
      expect(named(calls, "alert")).toEqual([["alert", feeds], ["alert", health]]);
    }, 120_000);
    it("an operator's reject closes the day's /start with a note, and alerts nothing", async () => {
      const { calls, acts } = recorder();
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
      const out = await withWorker(() => approveAndWait("2026-11-05"), { healthcheck: boom, checkFeeds: boom, weeklyRecap: boom, checkRunHealth: boom, alert: boom, healthcheckLog: boom });
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
      const out = await withWorker(() => approveAndWait("2026-09-28"), { storeFulltext }, (tasks) => {
        seen.push(tasks);
        return Promise.resolve({ tasks: tasks.length, results: { A1: "Body." }, outcome: "completed" });
      });
      expect(seen).toEqual([[["A1", "https://example.com/a1"]]]);
      expect(stored).toEqual([{ tasks: 1, results: { A1: "Body." }, outcome: "completed" }]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("goes on without full text when nothing answers on the fulltext queue", async () => {
      stored.length = 0;
      // The test server does not skip time while an activity task sits unclaimed, so the clock is
      // moved past the schedule-to-start timeout by hand.
      const out = await withWorker(async () => {
        const h = await start("2026-09-29");
        await env.sleep("6 minutes");
        await h.signal(approveSignal, { decision: "approve" });
        return h.result();
      }, { storeFulltext }, null);
      expect(stored).toEqual([{ tasks: 1, results: {}, outcome: "unavailable" }]);
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("records a skipped fetch without calling Python", async () => {
      stored.length = 0;
      let fetched = 0;
      await withWorker(() => approveAndWait("2026-10-01"), { storeFulltext, planFulltext: () => Promise.resolve({ tasks: [], skip: "disabled" as const }) }, () => {
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
      await withWorker(() => approveAndWait("2026-09-30"), { storeFulltext, planFulltext: () => Promise.resolve({ tasks: [], existing }) }, () => {
        fetched++;
        return Promise.resolve({ tasks: 0, results: {}, outcome: "completed" });
      });
      expect(fetched).toBe(0);
      expect(stored).toEqual([]);
    }, 120_000);
  });
  describe("threads are best-effort", () => {
    it("a linker that fails ships the digest and records why", async () => {
      const { seen, acts } = spy({ threadsLink: () => nonRetryable("linker down") });
      const out = await withWorker(() => approveAndWait("2026-10-02"), acts);
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
      const out = await withWorker(() => approveAndWait("2026-10-03"), acts);
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
      await withWorker(async () => {
        const h = await start("2026-10-06", { force: true });
        await h.signal(approveSignal, { decision: "approve" });
        return h.result();
      }, acts);
      expect(forced).toEqual([true]);
    }, 120_000);
    it("a finish that fails leaves the render a pointer to no context, and the digest ships", async () => {
      const { seen, acts } = spy({ threadsFinish: () => nonRetryable("db locked") });
      const out = await withWorker(() => approveAndWait("2026-10-04"), acts);
      expect(out.broadcast).toBe("sent");
      expect(seen.rendered).toEqual([{ runId: 1, name: "thread_context.json", sha256: "0".repeat(64) }]);
    }, 120_000);
  });
  describe("the tail: record, hold, send", () => {
    it("archives before the hold, and publishes, sends and records the shown headlines only after it", async () => {
      const { calls, acts } = tail();
      const out = await withWorker(() => approveAndWait("2026-10-02"), acts);
      expect(calls).toEqual(["archiveRun", "render", "sendEnabled", "notifyHold", "saveDigest", "broadcast", "recordShownHeadlines", "finishRun"]);
      expect(out).toMatchObject({ broadcast: "sent", recipients: 12 });
    }, 120_000);
    it("a rejected issue is neither published, sent nor recorded as shown", async () => {
      const { calls, acts } = tail();
      const out = await withWorker(async () => {
        const h = await start("2026-10-03");
        await h.signal(approveSignal, { decision: "reject" });
        return h.result();
      }, acts);
      expect(calls).toEqual(["archiveRun", "render", "sendEnabled", "finishRun"]); // rejected before the hold: no hold notice
      expect(out.broadcast).toBe("rejected");
    }, 120_000);
    it("a send that fails is not retried: the run is marked failed and the workflow fails", async () => {
      const { calls, acts } = tail({ broadcast: () => Promise.reject(new Error("read timeout")) });
      await expect(withWorker(() => approveAndWait("2026-10-04"), acts)).rejects.toThrow();
      expect(calls.filter((c) => c === "broadcast")).toHaveLength(1);
      expect(calls.slice(-2)).toEqual(["broadcast", "abortRun"]);
    }, 120_000);
    it("a hold notification that fails does not hold up the send", async () => {
      const { calls, acts } = tail({ notifyHold: () => Promise.reject(ApplicationFailure.nonRetryable("resend down")) });
      const out = await withWorker(() => approveAndWait("2026-10-05"), acts);
      expect(out.broadcast).toBe("sent");
      expect(calls).toContain("broadcast");
    }, 120_000);
    it("with the send disabled nothing is published, sent, held for or recorded as shown", async () => {
      const { calls, acts } = tail({ sendEnabled: () => Promise.resolve(false) });
      const out = await withWorker(async () => (await start("2026-10-06")).result(), acts);
      expect(calls).toEqual(["archiveRun", "render", "sendEnabled", "finishRun"]);
      expect(out).toMatchObject({ broadcast: "disabled" });
    }, 120_000);
    it("a failure recording after a delivered send is retried, not a failed run", async () => {
      let tries = 0;
      const { calls, acts } = tail({
        recordShownHeadlines: () => (++tries === 1 ? Promise.reject(new Error("SQLITE_BUSY: database is locked")) : Promise.resolve({ rows: 3 })),
        finishRun: () => (tries++ === 2 ? Promise.reject(new Error("SQLITE_BUSY: database is locked")) : Promise.resolve()),
      });
      const out = await withWorker(() => approveAndWait("2026-10-07"), acts);
      expect(out).toMatchObject({ broadcast: "sent" });
      expect(calls.filter((c) => c === "broadcast")).toHaveLength(1);
      expect(calls).not.toContain("abortRun");
    }, 120_000);
    it("the hold is cut to what the run's deadline leaves after the send, and the notice says when it ends", async () => {
      const ends: (string | null)[] = [];
      const { acts } = tail({ notifyHold: (_r, _s, at) => {
        ends.push(at);
        return Promise.resolve({ sent: true });
      } });
      const out = await withWorker(async () => {
        const h = await start("2026-10-08", {}, { workflowRunTimeout: "2 hours" });
        const { startTime } = await h.describe();
        const result = await h.result(); // no signal: the capped hold runs out, well inside the hour
        return { result, startTime };
      }, acts);
      expect(out.result.broadcast).toBe("sent");
      const held = Date.parse(ends[0]!) - out.startTime.getTime();
      // The deadline is the run timeout less its margin; the hold leaves the tail its margin of that.
      expect(held).toBeLessThanOrEqual(2 * 60 * 60 * 1000 - DEADLINE_MARGIN_MS - TAIL_MARGIN_MS + 1000); // the server's start time and the run's can differ by a millisecond
      expect(held).toBeGreaterThanOrEqual(HOLD_MINIMUM_MS);
    }, 120_000);
    it("with no budget left for a real review, the run is held out: not sent, the operator told, the switch failed", async () => {
      const { calls, acts } = tail();
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
      it("an operator's retry at 115 minutes still gets a held, sent digest (the reviewer's case)", async () => {
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
      it("the reviewer's mid-send case (retry at 108 minutes): the hold is cut to the deadline, so the send ends before it", async () => {
        const sends: string[] = [];
        const finished: string[] = [];
        const { calls, acts } = parkedOnce({
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
          await env.sleep("108 minutes");
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
