// usage: record-histories OUT_DIR
// Runs each representative DigestWorkflow path over the stub activities on the Temporal at
// TEMPORAL_ADDRESS (a dev server: `temporal server start-dev`) and writes each history as JSON, the
// fixtures replay.test.ts replays against the current workflow code. Re-record in the commit that
// changes the workflow's commands, from that commit's code.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Client, Connection, type WorkflowHandle } from "@temporalio/client";
import { ApplicationFailure } from "@temporalio/common";
import { historyToJSON } from "@temporalio/common/lib/proto-utils.js";
import { NativeConnection, Worker } from "@temporalio/worker";
import type { Activities, DigestInput, FulltextTask } from "../activities/index.js";
import { stubActivities } from "../activities/stub.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT } from "../workflow/digest.workflow.js";
import { PYTHON_TASK_QUEUE } from "../workflow/policy.js";
import { approveSignal, retrySignal } from "../workflow/signals.js";

type Scenario = { name: string; input?: Partial<DigestInput>; runTimeout?: string; activities?: Partial<Activities>; drive: (h: WorkflowHandle<typeof DigestWorkflow>) => Promise<unknown> };
const inHold = async (h: WorkflowHandle): Promise<void> => {
  for (let i = 0; i < 600; i++) {
    const events = (await h.fetchHistory()).events ?? [];
    const notified = events.findIndex((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === "notifyHold");
    if (notified >= 0 && events.slice(notified).some((e) => e.timerStartedEventAttributes)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("never reached the hold");
};
const signalled = (decision: "approve" | "reject") => async (h: WorkflowHandle<typeof DigestWorkflow>) => {
  await h.signal(approveSignal, { decision });
  return h.result();
};
// The decision arrives during the hold: the hold's timer is started, then cancelled by the signal.
const inHoldThen = (decision: "approve" | "reject") => async (h: WorkflowHandle<typeof DigestWorkflow>) => {
  await inHold(h);
  return signalled(decision)(h);
};
// A run that fails a pre-send check holds; one that passes sends at once.
const FLAGGED: Partial<Activities> = { checkPreSend: () => Promise.resolve(["TEST: a failed pre-send check"]) };
// A clean run's reject must land before its send: the checks end only once it has been delivered.
let rejectDelivered!: () => void;
const delivered = new Promise<void>((r) => (rejectDelivered = r));
const SLOW_CHECKS: Partial<Activities> = {
  checkPreSend: async () => {
    await delivered;
    return [];
  },
};
const rejectedEarly = async (h: WorkflowHandle<typeof DigestWorkflow>) => {
  await h.signal(approveSignal, { decision: "reject" });
  rejectDelivered();
  return h.result();
};
export const SCENARIOS: Scenario[] = [
  // Clean: sent with no hold, or rejected before the send.
  { name: "sent", drive: (h) => h.result() },
  { name: "rejected", activities: SLOW_CHECKS, drive: rejectedEarly },
  // Flagged: the hold's timer runs out and it sends. A 41 min run timeout leaves a 31 min deadline,
  // about a minute of hold after the tail's margin, so it records in a minute.
  { name: "flagged-sent", activities: FLAGGED, runTimeout: "41 minutes", drive: (h) => h.result() },
  { name: "approved-in-hold", activities: FLAGGED, drive: inHoldThen("approve") },
  { name: "rejected-in-hold", activities: FLAGGED, drive: inHoldThen("reject") },
  // A 40 min run timeout leaves a 30 min deadline, all of it the tail's margin: no budget for the send.
  { name: "held-out", runTimeout: "40 minutes", drive: (h) => h.result() },
  { name: "disabled", activities: { sendEnabled: () => Promise.resolve(false) }, drive: (h) => h.result() },
  { name: "resume", input: { resumeRun: 303 }, drive: (h) => h.result() },
  { name: "parked-abort", input: { failStage: "select" }, drive: async (h) => {
      await h.signal(retrySignal, { decision: "abort" });
      return h.result();
    } },
  { name: "failed", activities: { writeStory: () => Promise.reject(ApplicationFailure.nonRetryable("the model is gone", "Record")) }, drive: (h) => h.result().catch(() => undefined) },
  // A run a deploy would find: waiting in the pre-send hold. Recorded open, then terminated.
  { name: "in-hold", activities: FLAGGED, drive: async (h) => inHold(h) },
];

async function main(outDir: string, address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233"): Promise<void> {
  mkdirSync(outDir, { recursive: true });
  const client = new Client({ connection: await Connection.connect({ address }) });
  const connection = await NativeConnection.connect({ address });
  const nonce = Date.now().toString(36);
  const python = await Worker.create({
    connection,
    taskQueue: PYTHON_TASK_QUEUE,
    activities: {
      fetchFulltext: (tasks: FulltextTask[]) => Promise.resolve({ tasks: tasks.length, results: {}, outcome: "completed" }),
    },
  });
  await python.runUntil(async () => {
    for (const s of SCENARIOS) {
      const taskQueue = `record-${s.name}-${nonce}`;
      const worker = await Worker.create({ connection, taskQueue, workflowsPath: new URL("../workflow/digest.workflow.js", import.meta.url).pathname, activities: { ...stubActivities(), ...s.activities } });
      await worker.runUntil(async () => {
        const h = await client.workflow.start(DigestWorkflow, { taskQueue, workflowId: `digest-${s.name}-${nonce}`, args: [{ runDate: "2026-09-23", ...s.input }], workflowRunTimeout: s.runTimeout ?? WORKFLOW_RUN_TIMEOUT });
        await s.drive(h);
        writeFileSync(join(outDir, `${s.name}.json`), `${historyToJSON(await h.fetchHistory())}\n`);
        if (s.name === "in-hold") await h.terminate("recorded in the hold");
        console.log(`${s.name}: ${(await h.describe()).status.name}`);
      });
    }
  });
}
if (process.argv[1]?.endsWith("record-histories.js")) await main(process.argv[2] ?? "src/workflow/histories");
