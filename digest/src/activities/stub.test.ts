import { describe, expect, it } from "vitest";
import { stubActivities } from "./stub.js";

describe("stub activities", () => {
  it("startRun honours resumeRun and defaults to 1", async () => {
    const a = stubActivities();
    expect(await a.startRun({ runDate: "2026-09-21" })).toEqual({ runId: 1 });
    expect(await a.startRun({ runDate: "2026-09-21", resumeRun: 303, force: true })).toEqual({ runId: 303 });
  });
  it("select throws non-retryably only when the input asks it to", async () => {
    const a = stubActivities();
    const p = { runId: 1, name: "x", sha256: "0".repeat(64) };
    await expect(a.select(1, p, p, undefined, { runDate: "d", failStage: "select" })).rejects.toThrow(/select failed/);
    await expect(a.select(1, p, p)).resolves.toMatchObject({ name: "selected.json" });
  });
});
