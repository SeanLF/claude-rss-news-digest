import { Client, Connection, ScheduleAlreadyRunning, ScheduleOverlapPolicy, type ScheduleOptions, type WorkflowStartOptions } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import type { DigestInput } from "./activities/index.js";
import { TASK_QUEUE, temporalNamespace } from "./worker.js";
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
    // A resume or a forced re-run may reuse a completed day's id (spec §2.1); a running one never.
    workflowIdReusePolicy: opts.force || opts.resumeRun !== undefined ? WorkflowIdReusePolicy.ALLOW_DUPLICATE : WorkflowIdReusePolicy.REJECT_DUPLICATE,
  };
}

// `start [YYYY-MM-DD] [--force] [--resume N]`. A new run's issue is dated the day it starts (UTC,
// runs.started_at), so a new run may name only today; the date is otherwise just the workflow id
// (digest-<date>, which approve and reject signal). A resume may name the day of the run it resumes.
export function parseStartArgs(argv: string[], today = new Date().toISOString().slice(0, 10)): { date: string; opts: StartOpts & { force: boolean } } {
  let date: string | undefined;
  let force = false;
  let resumeRun: number | undefined;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--force") force = true;
    else if (a === "--resume") {
      const n = Number(argv[++i]);
      if (!Number.isInteger(n) || n <= 0) throw new Error("--resume takes a run number, e.g. --resume 300");
      resumeRun = n;
    } else if (!a.startsWith("--") && date === undefined) date = a;
    else throw new Error(`unknown argument ${a}; usage: start [YYYY-MM-DD] [--force] [--resume N]`);
  }
  // A resume names its day: under today's id it would take the id today's own run needs.
  if (resumeRun !== undefined && date === undefined) throw new Error("--resume needs the day of the run it resumes, e.g. start 2026-09-18 --resume 300");
  if (date !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error(`${date} is not a YYYY-MM-DD date`);
  if (date !== undefined && date !== today && resumeRun === undefined)
    throw new Error(`a new run's issue is dated the day it starts, ${today} (UTC), not ${date}; start it today (with --force to run today again), or name a past day only with --resume N`);
  return { date: date ?? today, opts: { force, ...(resumeRun !== undefined ? { resumeRun } : {}) } };
}

// 12:25 Europe/Paris daily, as the systemd timer ran (10:25Z in summer, 11:25Z in winter), so a clock
// change needs no watcher; overlap: skip; catch-up: one day. Replaces the timer and reboot catch-up.
// The scheduled action's workflowId is fixed; plan A2's startRun derives the run date from the
// start time when runDate is empty, and the overlap policy covers scheduled starts.
export function scheduleOptions(): ScheduleOptions {
  return {
    scheduleId: SCHEDULE_ID,
    spec: { calendars: [{ hour: 12, minute: 25 }], timezone: "Europe/Paris" },
    policies: { overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" },
    // Created paused; only the deploy's live-pipeline switch unpauses it. The update in
    // ensureSchedule keeps whatever state the schedule already has.
    state: { paused: true, note: "created paused; the live-pipeline switch unpauses it" },
    action: { type: "startWorkflow", workflowType: DigestWorkflow, taskQueue: TASK_QUEUE, workflowId: "digest-scheduled", args: [{ runDate: "" }], workflowRunTimeout: WORKFLOW_RUN_TIMEOUT },
  };
}

export async function connect(address = process.env["TEMPORAL_ADDRESS"] ?? DEFAULT_ADDRESS): Promise<Client> {
  return new Client({ connection: await Connection.connect({ address }), namespace: temporalNamespace() });
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
