import { describe, expect, it } from "vitest";
import { scorePlanted } from "./planted-score.js";

const s = (h: string, id: string) => ({ headline: h, sources: [{ article_id: id }] });
const draft = { must_know: [s("A", "A1"), s("B", "A2")], should_know: [s("C", "A3")] };
const key = { hard_positives: [{ idx: 0, field: "summary" as const }, { idx: 2, field: "headline" as const }], clean_fields: [{ idx: 1, field: "summary" as const }, { idx: 1, field: "headline" as const }] };

describe("scorePlanted", () => {
  it("counts plants caught, clean fields dropped, and names both, by draft index", () => {
    const report = { results: [
      { headline: "A", article_ids: ["A1"], pass: false, reason: "r", failed_fields: ["summary" as const] },
      { headline: "B", article_ids: ["A2"], pass: false, reason: "r", failed_fields: ["summary" as const] },
      { headline: "C", article_ids: ["A3"], pass: true, reason: "ok" },
    ] };
    expect(scorePlanted(report, draft, key)).toEqual({ recall: 1, planted: 2, falseDrops: 1, clean: 2, missed: ["2:headline"], dropped: ["1:summary"] });
  });
});
