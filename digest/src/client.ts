import { Client, Connection, ScheduleOverlapPolicy, type WorkflowStartOptions } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import type { DigestInput } from "./activities/index.js";
import { TASK_QUEUE } from "./worker.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./workflow/digest.workflow.js";

export type StartOpts = { resumeRun?: number; force?: boolean };
const DEFAULT_ADDRESS = "localhost:7233";

// The id policy the spec states: a duplicate is rejected while one runs; after completion a new
// one is allowed only when the start carries force (the successor of today's --force).
export function startOptions(runDate: string, opts: StartOpts): WorkflowStartOptions<typeof DigestWorkflow> {
  const input: DigestInput = { runDate, ...(opts.force ? { force: true } : {}), ...(opts.resumeRun !== undefined ? { resumeRun: opts.resumeRun } : {}) };
  return {
    taskQueue: TASK_QUEUE,
    workflowId: workflowIdFor(runDate),
    args: [input],
    workflowRunTimeout: WORKFLOW_RUN_TIMEOUT,
    workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL,
    workflowIdReusePolicy: opts.force ? WorkflowIdReusePolicy.ALLOW_DUPLICATE : WorkflowIdReusePolicy.REJECT_DUPLICATE,
  };
}

async function client(address: string): Promise<Client> {
  return new Client({ connection: await Connection.connect({ address }) });
}

export async function startDigest(runDate: string, opts: StartOpts = {}, address = process.env["TEMPORAL_ADDRESS"] ?? DEFAULT_ADDRESS) {
  return (await client(address)).workflow.start(DigestWorkflow, startOptions(runDate, opts));
}

// 10:25Z daily; overlap: skip; catch-up: one day. Replaces the systemd timer and reboot catch-up.
export async function ensureSchedule(address = process.env["TEMPORAL_ADDRESS"] ?? DEFAULT_ADDRESS) {
  await (await client(address)).schedule.create({
    scheduleId: "digest-daily",
    spec: { calendars: [{ hour: 10, minute: 25 }] },
    policies: { overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" },
    action: { type: "startWorkflow", workflowType: DigestWorkflow, taskQueue: TASK_QUEUE, workflowId: "digest-scheduled", args: [{ runDate: "" }] },
  });
}
