import { describe, expect, it } from "vitest";
import { PREHEADER_MAX_CHARS, SelectionsSchema } from "./selections.js";

const story = { headline: "H", summary: "S", why_it_matters: "W", sources: [{ article_id: "A1" }] };

describe("selections contract, mirrored from newsroom/src/schema.py", () => {
  it("accepts the shipped shape", () => {
    expect(SelectionsSchema.safeParse({ must_know: [story], should_know: [], preheader: "p" }).success).toBe(true);
  });
  it("a source is {article_id} and nothing else; angle and bias are resolved by code after validation", () => {
    const bad = { ...story, sources: [{ article_id: "A1", angle: "a", bias: "centre" }] };
    expect(SelectionsSchema.safeParse({ must_know: [bad], should_know: [], preheader: "p" }).success).toBe(false);
  });
  it("rejects a preheader over the cap", () => {
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [], preheader: "x".repeat(PREHEADER_MAX_CHARS + 1) }).success).toBe(false);
  });
  it("rejects cluster_index: it left the contract", () => {
    expect(SelectionsSchema.safeParse({ must_know: [{ ...story, cluster_index: 3 }], should_know: [], preheader: "p" }).success).toBe(false);
  });
  it("a brief may omit why_it_matters and may carry one (tolerated, per the Python)", () => {
    const { why_it_matters: _w, ...brief } = story;
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [brief], preheader: "p" }).success).toBe(true);
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [story], preheader: "p" }).success).toBe(true);
  });
  it("a story needs at least one source and a must_know needs why_it_matters", () => {
    expect(SelectionsSchema.safeParse({ must_know: [{ ...story, sources: [] }], should_know: [], preheader: "p" }).success).toBe(false);
    const { why_it_matters: _w, ...noWhy } = story;
    expect(SelectionsSchema.safeParse({ must_know: [noWhy], should_know: [], preheader: "p" }).success).toBe(false);
  });
});
