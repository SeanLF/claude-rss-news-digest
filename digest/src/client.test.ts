import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { describe, expect, it } from "vitest";
import { startOptions } from "./client.js";

describe("startOptions", () => {
  it("a normal day rejects duplicates while running and after completion", () => {
    const o = startOptions("2026-09-21", {});
    expect(o.workflowId).toBe("digest-2026-09-21");
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.REJECT_DUPLICATE);
    expect(o.workflowRunTimeout).toBe("4 hours");
    expect(o.args).toEqual([{ runDate: "2026-09-21" }]);
  });
  it("a forced re-run may reuse the id of a completed run, never a running one", () => {
    const o = startOptions("2026-09-21", { force: true, resumeRun: 303 });
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.ALLOW_DUPLICATE);
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.args).toEqual([{ runDate: "2026-09-21", force: true, resumeRun: 303 }]);
  });
});
