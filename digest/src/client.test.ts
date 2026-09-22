import { ScheduleAlreadyRunning, ScheduleOverlapPolicy, type Client } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { describe, expect, it } from "vitest";
import { ensureSchedule, SCHEDULE_ID, scheduleOptions, startOptions } from "./client.js";

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

describe("the daily schedule", () => {
  it("runs at 10:25Z, skips overlap, catches up one day", () => {
    const o = scheduleOptions();
    expect(o.scheduleId).toBe(SCHEDULE_ID);
    expect(o.spec.calendars).toEqual([{ hour: 10, minute: 25 }]);
    expect(o.policies).toEqual({ overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" });
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
            const next = fn({}) as { spec: unknown };
            expect(next.spec).toEqual(scheduleOptions().spec);
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
