import { Client, Connection, ScheduleAlreadyRunning, ScheduleOverlapPolicy, type ScheduleOptions, type WorkflowStartOptions } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import type { DigestInput } from "./activities/index.js";
import { TASK_QUEUE } from "./worker.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./workflow/digest.workflow.js";

export type StartOpts = { resumeRun?: number; force?: boolean };
export const SCHEDULE_ID = "digest-daily";
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

// 10:25Z daily; overlap: skip; catch-up: one day. Replaces the systemd timer and reboot catch-up.
// The scheduled action's workflowId is fixed; plan A2's startRun derives the run date from the
// start time when runDate is empty, and the overlap policy covers scheduled starts.
export function scheduleOptions(): ScheduleOptions {
  return {
    scheduleId: SCHEDULE_ID,
    spec: { calendars: [{ hour: 10, minute: 25 }] },
    policies: { overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" },
    action: { type: "startWorkflow", workflowType: DigestWorkflow, taskQueue: TASK_QUEUE, workflowId: "digest-scheduled", args: [{ runDate: "" }] },
  };
}

export async function connect(address = process.env["TEMPORAL_ADDRESS"] ?? DEFAULT_ADDRESS): Promise<Client> {
  return new Client({ connection: await Connection.connect({ address }) });
}

export async function startDigest(client: Client, runDate: string, opts: StartOpts = {}) {
  return client.workflow.start(DigestWorkflow, startOptions(runDate, opts));
}

// Idempotent: a schedule that already exists is updated to the current options, not an error.
export async function ensureSchedule(client: Client): Promise<"created" | "updated"> {
  const options = scheduleOptions();
  try {
    await client.schedule.create(options);
    return "created";
  } catch (e) {
    if (!(e instanceof ScheduleAlreadyRunning)) throw e;
    await client.schedule.getHandle(SCHEDULE_ID).update((prev) => ({ ...prev, spec: options.spec, action: options.action, ...(options.policies ? { policies: options.policies } : {}) }));
    return "updated";
  }
}
