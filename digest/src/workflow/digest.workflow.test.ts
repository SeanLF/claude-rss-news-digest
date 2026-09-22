import { WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { stubActivities } from "../activities/stub.js";
import { DigestWorkflow, workflowIdFor } from "./digest.workflow.js";
import { approveSignal, retrySignal } from "./signals.js";

let env: TestWorkflowEnvironment;
beforeAll(async () => {
  env = await TestWorkflowEnvironment.createTimeSkipping();
}, 180_000);
afterAll(async () => {
  await env?.teardown();
});

const taskQueue = "digest-test";
async function withWorker<T>(fn: () => Promise<T>): Promise<T> {
  const worker = await Worker.create({
    connection: env.nativeConnection,
    taskQueue,
    workflowsPath: new URL("./digest.workflow.ts", import.meta.url).pathname,
    activities: stubActivities(),
  });
  return worker.runUntil(fn());
}
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
  it("resumeRun without force is refused", async () => {
    await withWorker(async () => {
      await expect((await start("2026-09-27", { resumeRun: 303 })).result()).rejects.toThrow();
    });
  }, 120_000);
});
