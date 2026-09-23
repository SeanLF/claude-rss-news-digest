import { describe, expect, it } from "vitest";
import extractAssert, { boilerplateShare, tokenF1 } from "./extract-score.js";

describe("extract scoring", () => {
  it("token F1 is 1 on identical text, 0 on disjoint text, and counts repeats as a multiset", () => {
    expect(tokenF1("The vote passed", "the VOTE passed")).toBe(1);
    expect(tokenF1("alpha beta", "gamma delta")).toBe(0);
    expect(tokenF1("a a a a", "a")).toBeCloseTo(0.4);
  });
  it("boilerplate share counts matching non-empty lines", () => {
    expect(boilerplateShare("Body text.\n\nSubscribe to our newsletter\nMore body.")).toBeCloseTo(1 / 3);
  });
  it("marks a pair with a failed side as not comparable", () => {
    const short = extractAssert("too short", { vars: { reference: "x".repeat(300) } });
    expect(short.namedScores).toMatchObject({ success: 0, f1: -1 });
    const long = "word ".repeat(60);
    expect(extractAssert(long, { vars: { reference: long } }).namedScores).toMatchObject({ success: 1, f1: 1 });
  });
});
