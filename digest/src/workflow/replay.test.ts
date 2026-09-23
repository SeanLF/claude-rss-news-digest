import { readdirSync, readFileSync } from "node:fs";
import { Worker } from "@temporalio/worker";
import { describe, expect, it } from "vitest";
import { changedWorkflowPath } from "./deploy-variant.js";

// Recorded histories of every representative path (src/cli/record-histories.ts), replayed against
// the workflow code as it is now: what a worker restart within one build does to a run in flight.
// A change to the workflow's commands fails here; re-record in the same commit (top of digest.workflow.ts).
const dir = new URL("./histories/", import.meta.url);
const fixtures = readdirSync(dir)
  .filter((f) => f.endsWith(".json"))
  .toSorted()
  .map((f) => ({ workflowId: f.replace(/\.json$/, ""), history: JSON.parse(readFileSync(new URL(f, dir), "utf8")) as unknown }));
type RecordedEvent = { eventType?: string; activityTaskScheduledEventAttributes?: { activityType?: { name?: string } } };
const currentCode = new URL("./digest.workflow.ts", import.meta.url).pathname;

async function replay(workflowsPath: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for await (const r of Worker.runReplayHistories({ workflowsPath }, fixtures)) out[r.workflowId] = r.error ? r.error.name : "ok";
  return out;
}

describe("workflow replay", () => {
  it("covers every representative path", () => {
    expect(fixtures.map((f) => f.workflowId)).toEqual(["approved-in-hold", "disabled", "failed", "held-out", "in-hold", "parked-abort", "rejected-in-hold", "rejected", "resume", "sent"]);
  });

  // Control on the fixtures themselves: the decision landed during the hold, not before it.
  it.each([
    ["approved-in-hold", "broadcast"],
    ["rejected-in-hold", undefined],
  ])("%s: the decision cancels the hold's timer after notifyHold", (name, sendAfter) => {
    const history = fixtures.find((f) => f.workflowId === name)?.history as { events?: RecordedEvent[] } | undefined;
    const types = (history?.events ?? []).map((e) => e.activityTaskScheduledEventAttributes?.activityType?.name ?? e.eventType ?? "");
    const notified = types.indexOf("notifyHold");
    const cancelled = types.indexOf("EVENT_TYPE_TIMER_CANCELED", notified);
    expect(notified).toBeGreaterThanOrEqual(0);
    expect(types.slice(notified).includes("EVENT_TYPE_TIMER_STARTED")).toBe(true);
    expect(cancelled).toBeGreaterThan(notified);
    expect(types.slice(cancelled).includes("broadcast")).toBe(sendAfter !== undefined);
  });

  it("every recorded history replays against the current workflow code", async () => {
    expect(await replay(currentCode)).toEqual(Object.fromEntries(fixtures.map((f) => [f.workflowId, "ok"])));
  }, 120_000);

  it("negative control: an activity added before the hold fails every history that got that far", async () => {
    expect(await replay(changedWorkflowPath())).toEqual({
      "approved-in-hold": "DeterminismViolationError",
      disabled: "ok", // ends before the hold
      failed: "ok",
      "held-out": "DeterminismViolationError",
      "in-hold": "DeterminismViolationError",
      "parked-abort": "ok",
      rejected: "ok", // rejected before the hold was reached
      "rejected-in-hold": "DeterminismViolationError",
      resume: "DeterminismViolationError",
      sent: "DeterminismViolationError",
    });
  }, 120_000);
});
