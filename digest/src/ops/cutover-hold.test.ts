import { describe, expect, it } from "vitest";
import { cutoverHold, isCutoverHold } from "./cutover-hold.js";

describe("cutoverHold", () => {
  it("holds every run dated on or before HOLD_ALWAYS_THROUGH, and says no check failed", () => {
    for (const day of ["2026-09-28", "2026-10-01"]) expect(cutoverHold({ HOLD_ALWAYS_THROUGH: "2026-10-01" }, day)).toBe("CUTOVER_HOLD: every run through 2026-10-01 holds for the cut-over (HOLD_ALWAYS_THROUGH); no check failed");
  });
  it("lapses on its own the day after, so it cannot be left on", () => {
    expect(cutoverHold({ HOLD_ALWAYS_THROUGH: "2026-10-01" }, "2026-10-02")).toBeNull();
  });
  it("is off when unset or empty", () => {
    expect(cutoverHold({}, "2026-09-28")).toBeNull();
    expect(cutoverHold({ HOLD_ALWAYS_THROUGH: " " }, "2026-09-28")).toBeNull();
  });
  it("a value that is not a calendar date holds and names it, rather than silently not holding", () => {
    for (const v of ["true", "2026-10-1", "2026-02-30", "2026-99-99", "01-10-2026", '"2026-10-01"']) expect(cutoverHold({ HOLD_ALWAYS_THROUGH: v }, "2026-09-28")).toMatch(/^CUTOVER_HOLD: HOLD_ALWAYS_THROUGH='.*' is not a YYYY-MM-DD date/);
  });
  it("its line is told apart from a failed check's", () => {
    expect(isCutoverHold(cutoverHold({ HOLD_ALWAYS_THROUGH: "2026-10-01" }, "2026-09-28")!)).toBe(true);
    expect(isCutoverHold("INTERNAL_ID_LEAK: 1 leak(s)")).toBe(false);
  });
});
