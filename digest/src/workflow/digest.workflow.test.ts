import { WorkflowFailedError } from "@temporalio/client";
import { ApplicationFailure, WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Activities, AlertRequest, FulltextFetch, FulltextTask } from "../activities/index.js";
import { stubActivities } from "../activities/stub.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./digest.workflow.js";
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
      const out = await withWorker(() => approveAndWait("2026-10-02"), acts);
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
      await withWorker(() => approveAndWait("2026-10-03"), acts);
      expect(named(calls, "alert")).toEqual([["alert", feeds], ["alert", health]]);
    }, 120_000);
    it("a rejected run is checked as not broadcasting and never pings success", async () => {
      const { calls, acts } = recorder();
      await withWorker(async () => {
        const h = await start("2026-10-04");
        await h.signal(approveSignal, { decision: "reject" });
        return h.result();
      }, acts);
      expect(named(calls, "checkRunHealth")).toEqual([["checkRunHealth", 1, false]]);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"]]);
    }, 120_000);
    it("the operations checks are best-effort: every one failing still delivers the run", async () => {
      const boom = failed("ops down");
      const out = await withWorker(() => approveAndWait("2026-10-05"), { healthcheck: boom, checkFeeds: boom, weeklyRecap: boom, checkRunHealth: boom, alert: boom, healthcheckLog: boom });
      expect(out.broadcast).toBe("sent");
    }, 120_000);
    it("a failed run pings fail and alerts with the cause, then still fails", async () => {
      const { calls, acts } = recorder({ writeStory: failed("write s00: the model is gone") });
      const err = await withWorker(async () => (await start("2026-10-06")).result().catch((e: unknown) => e), acts);
      expect(err).toBeInstanceOf(WorkflowFailedError);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "fail"]]);
      const [[, req]] = named(calls, "alert") as [[string, AlertRequest]];
      expect(req).toMatchObject({ kind: "run-failed", workflowId: workflowIdFor("2026-10-06"), runId: 1, timedOut: false });
      expect((req as Extract<AlertRequest, { kind: "run-failed" }>).reason).toContain("write s00: the model is gone");
    }, 120_000);
    it("a run that hangs fails loudly at its own deadline, before the server's run timeout can kill it silently", async () => {
      const { calls, acts } = recorder();
      // Parked on the retry signal with nobody answering: only the deadline ends it.
      const err = await withWorker(async () => (await start("2026-10-07", { failStage: "select" }, { workflowRunTimeout: WORKFLOW_RUN_TIMEOUT })).result().catch((e: unknown) => e), acts);
      expect(err).toBeInstanceOf(WorkflowFailedError);
      expect((err as WorkflowFailedError).cause?.message).toMatch(/deadline/);
      expect(named(calls, "alert")).toMatchObject([["alert", { kind: "run-failed", runId: 1, timedOut: true }]]);
      expect(named(calls, "healthcheck")).toEqual([["healthcheck", "start"], ["healthcheck", "fail"]]);
    }, 120_000);
    it("a failed resume alerts but leaves the dead-man's switch to the day's first run", async () => {
      const { calls, acts } = recorder({ writeStory: failed("still gone") });
      await withWorker(async () => (await start("2026-10-08", { resumeRun: 303 })).result().catch((e: unknown) => e), acts);
      expect(named(calls, "healthcheck")).toEqual([]);
      expect(named(calls, "alert")).toMatchObject([["alert", { kind: "run-failed", runId: 303 }]]);
    }, 120_000);
    it("an operator's cancellation is not a failure to alert on", async () => {
      const { calls, acts } = recorder();
      await withWorker(async () => {
        const h = await start("2026-10-09");
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
});
