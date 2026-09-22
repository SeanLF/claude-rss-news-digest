import { describe, expect, it } from "vitest";
import { selfAgreement } from "./band.js";
import type { Criterion } from "./verdict.js";
const v = (story: number, criterion: Criterion, pass: boolean) => ({ story, criterion, pass, reason: "" });
describe("selfAgreement", () => {
  it("is 1 when every run agrees and 0.5 when half the cells flip", () => {
    expect(selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, true)]]).overall).toBe(1);
    const half = selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, false)]]);
    expect(half.overall).toBe(0.5);
    expect(half.perCriterion).toEqual({ 1: 1, 2: 0 });
  });
  it("is 0 with no verdicts rather than NaN", () => {
    expect(selfAgreement([]).overall).toBe(0);
  });
});
