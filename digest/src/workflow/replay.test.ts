import { readdirSync, readFileSync } from "node:fs";
import { Worker } from "@temporalio/worker";
import { describe, expect, it } from "vitest";
import { changedWorkflowPath } from "./deploy-variant.js";

// Recorded histories of every representative path (src/cli/record-histories.ts), replayed against
// the workflow code as it is now. A change that would break a run in flight across a deploy fails
// here: gate it with patched() (see the top of digest.workflow.ts), never re-record to pass.
const dir = new URL("./histories/", import.meta.url);
const fixtures = readdirSync(dir)
  .filter((f) => f.endsWith(".json"))
  .toSorted()
  .map((f) => ({ workflowId: f.replace(/\.json$/, ""), history: JSON.parse(readFileSync(new URL(f, dir), "utf8")) as unknown }));
const currentCode = new URL("./digest.workflow.ts", import.meta.url).pathname;

async function replay(workflowsPath: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for await (const r of Worker.runReplayHistories({ workflowsPath }, fixtures)) out[r.workflowId] = r.error ? r.error.name : "ok";
  return out;
}

describe("workflow replay", () => {
  it("covers every representative path", () => {
    expect(fixtures.map((f) => f.workflowId)).toEqual(["disabled", "failed", "held-out", "in-hold", "parked-abort", "rejected", "resume", "sent"]);
  });

  it("every recorded history replays against the current workflow code", async () => {
    expect(await replay(currentCode)).toEqual(Object.fromEntries(fixtures.map((f) => [f.workflowId, "ok"])));
  }, 120_000);

  it("negative control: an activity added before the hold without patched() fails every history that got that far", async () => {
    expect(await replay(changedWorkflowPath("bare"))).toEqual({
      disabled: "ok", // ends before the hold
      failed: "ok",
      "held-out": "DeterminismViolationError",
      "in-hold": "DeterminismViolationError",
      "parked-abort": "ok",
      rejected: "ok", // rejected before the hold was reached
      resume: "DeterminismViolationError",
      sent: "DeterminismViolationError",
    });
  }, 120_000);

  it("the same change behind patched() replays every history", async () => {
    expect(await replay(changedWorkflowPath("patched"))).toEqual(Object.fromEntries(fixtures.map((f) => [f.workflowId, "ok"])));
  }, 120_000);
});
