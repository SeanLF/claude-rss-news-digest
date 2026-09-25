import { ScheduleAlreadyRunning, ScheduleOverlapPolicy, type Client } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { describe, expect, it } from "vitest";
import { ensureSchedule, parseStartArgs, SCHEDULE_ID, scheduleOptions, startOptions } from "./client.js";
import { temporalNamespace } from "./worker.js";
import { WORKFLOW_RUN_TIMEOUT } from "./workflow/digest.workflow.js";

describe("startOptions", () => {
  it("a normal day rejects duplicates while running and after completion", () => {
    const o = startOptions("2026-09-21", {});
    expect(o.workflowId).toBe("digest-2026-09-21");
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.REJECT_DUPLICATE);
    expect(o.workflowRunTimeout).toBe("4 hours");
    expect(o.args).toEqual([{ runDate: "2026-09-21" }]);
  });
  it("a resume without force may also reuse a completed day's id", () => {
    expect(startOptions("2026-09-21", { resumeRun: 303 }).workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.ALLOW_DUPLICATE);
  });
  it("a forced re-run may reuse the id of a completed run, never a running one", () => {
    const o = startOptions("2026-09-21", { force: true, resumeRun: 303 });
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.ALLOW_DUPLICATE);
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.args).toEqual([{ runDate: "2026-09-21", force: true, resumeRun: 303 }]);
  });
});

// A new run's issue is dated the day it starts (runs.started_at, UTC): the date a start names only
// names the workflow. Naming another day would file today's issue under a workflow id that says otherwise.
describe("make digest-start's arguments", () => {
  const today = "2026-09-24";
  it("with no date, starts today (UTC)", () => {
    expect(parseStartArgs([], today)).toEqual({ date: today, opts: { force: false } });
  });
  it("accepts today, and --force for a second run of it", () => {
    expect(parseStartArgs([today, "--force"], today)).toEqual({ date: today, opts: { force: true } });
    expect(parseStartArgs(["--force"], today)).toEqual({ date: today, opts: { force: true } });
  });
  it("refuses a new run for any other day, past or future, force or not", () => {
    expect(() => parseStartArgs(["2026-09-26"], today)).toThrow(/2026-09-24 .*not 2026-09-26/);
    expect(() => parseStartArgs(["2026-09-23", "--force"], today)).toThrow(/not 2026-09-23/);
  });
  it("a resume may name the day of the run it resumes", () => {
    expect(parseStartArgs(["2026-09-18", "--resume", "300"], today)).toEqual({ date: "2026-09-18", opts: { force: false, resumeRun: 300 } });
  });
  it("refuses what is not a date or not a run number", () => {
    expect(() => parseStartArgs(["24-09-2026"], today)).toThrow(/YYYY-MM-DD/);
    expect(() => parseStartArgs([today, "--resume"], today)).toThrow(/--resume/);
    expect(() => parseStartArgs([today, "--resume", "x"], today)).toThrow(/--resume/);
    expect(() => parseStartArgs([today, "--frce"], today)).toThrow(/--frce/);
    expect(() => parseStartArgs(["--resume", "300"], today)).toThrow(/--resume needs the day/);
  });
});

describe("the daily schedule", () => {
  it("runs at 12:25 Paris time (the Python timer's), skips overlap, catches up one day", () => {
    const o = scheduleOptions();
    expect(o.scheduleId).toBe(SCHEDULE_ID);
    // In the zone, not in UTC: 10:25Z in summer and 11:25Z in winter, as the systemd timer ran.
    expect(o.spec.calendars).toEqual([{ hour: 12, minute: 25 }]);
    expect(o.spec.timezone).toBe("Europe/Paris");
    expect(o.policies).toEqual({ overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" });
  });
  it("is created paused: only the live-pipeline switch unpauses it", () => {
    expect(scheduleOptions().state).toMatchObject({ paused: true });
  });
  it("a scheduled start has the same run budget as a manual one, which the hold and the deadline are cut to", () => {
    expect(scheduleOptions().action).toMatchObject({ workflowRunTimeout: WORKFLOW_RUN_TIMEOUT });
    expect(startOptions("2026-09-21", {}).workflowRunTimeout).toBe(WORKFLOW_RUN_TIMEOUT);
  });
  it("ensureSchedule creates once and updates in place when the schedule already exists", async () => {
    const calls: string[] = [];
    let exists = false;
    const fake = {
      schedule: {
        create: () => {
          calls.push("create");
          if (exists) return Promise.reject(new ScheduleAlreadyRunning("exists", SCHEDULE_ID));
          exists = true;
          return Promise.resolve({});
        },
        getHandle: (id: string) => ({
          update: (fn: (prev: unknown) => unknown) => {
            calls.push(`update:${id}`);
            const next = fn({ state: { paused: false, note: "live pipeline: temporal" } }) as { spec: unknown; state: unknown };
            expect(next.spec).toEqual(scheduleOptions().spec);
            expect(next.state).toEqual({ paused: false, note: "live pipeline: temporal" }); // an update never re-pauses a live schedule
            return Promise.resolve();
          },
        }),
      },
    } as unknown as Client;
    expect(await ensureSchedule(fake)).toBe("created");
    expect(await ensureSchedule(fake)).toBe("updated");
    expect(calls).toEqual(["create", "create", `update:${SCHEDULE_ID}`]);
  });
  it("ensureSchedule rethrows anything but already-running", async () => {
    const fake = { schedule: { create: () => Promise.reject(new Error("connection refused")) } } as unknown as Client;
    await expect(ensureSchedule(fake)).rejects.toThrow(/connection refused/);
  });
});

describe("the Temporal namespace", () => {
  it("is TEMPORAL_NAMESPACE when set, so production runs in the repo's own namespace", () => {
    expect(temporalNamespace({ TEMPORAL_NAMESPACE: "news-digest" })).toBe("news-digest");
  });
  it("is the dev server's default otherwise", () => {
    expect(temporalNamespace({})).toBe("default");
  });
});
