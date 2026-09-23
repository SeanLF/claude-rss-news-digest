import { WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Activities, FulltextFetch, FulltextTask } from "../activities/index.js";
import { stubActivities } from "../activities/stub.js";
import { DigestWorkflow, workflowIdFor } from "./digest.workflow.js";
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
  const python = await Worker.create({ connection: env.nativeConnection, taskQueue: PYTHON_TASK_QUEUE, activities: { fetchFulltext: fetcher } });
  return python.runUntil(worker.runUntil(fn()));
}
const approveAndWait = async (runDate: string) => {
  const h = await start(runDate);
  await h.signal(approveSignal, { decision: "approve" });
  return h.result();
};
const start = (runDate: string, extra: Record<string, unknown> = {}, opts: Record<string, unknown> = {}) =>
  env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor(runDate), args: [{ runDate, ...extra }], ...opts });

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
    it("goes on without full text when nothing answers on the python queue", async () => {
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
