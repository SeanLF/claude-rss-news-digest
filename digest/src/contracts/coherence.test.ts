import { describe, expect, it } from "vitest";
import { CoherenceReportSchema, coherenceReportJsonSchema } from "./coherence.js";

describe("coherence contract", () => {
  it("accepts a pass and a fail entry", () => {
    const r = CoherenceReportSchema.safeParse({
      results: [
        { headline: "H1", article_ids: ["A1"], pass: true, reason: "ok" },
        { headline: "H2", article_ids: ["A5"], pass: false, reason: "summary: x", failed_fields: ["summary"], failure_kinds: { summary: "contradicted" } },
      ],
    });
    expect(r.success).toBe(true);
  });
  it("rejects an unknown field name and an unknown kind", () => {
    expect(CoherenceReportSchema.safeParse({ results: [{ headline: "H", article_ids: [], pass: false, reason: "r", failed_fields: ["title"] }] }).success).toBe(false);
    expect(CoherenceReportSchema.safeParse({ results: [{ headline: "H", article_ids: [], pass: false, reason: "r", failure_kinds: { summary: "fabricated" } }] }).success).toBe(false);
  });
  it("the JSON schema constrains shape, never count, and targets draft-07", () => {
    const schema = coherenceReportJsonSchema();
    const json = JSON.stringify(schema);
    for (const key of ["minItems", "maxItems", "minLength", "maxLength"]) expect(json).not.toContain(key);
    expect(schema["$schema"]).toBe("http://json-schema.org/draft-07/schema#");
  });
});
