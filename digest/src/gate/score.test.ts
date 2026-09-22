import { describe, expect, it } from "vitest";
import { disagreements, recallOnPlants } from "./score.js";
describe("score", () => {
  it("recall counts a plant caught when its story's field is flagged", () => {
    const plants = [{ storyIndex: 1, field: "summary" as const, kind: "wrong-number" as const, original: "58%", planted: "117%" }];
    const report = { results: [{ headline: "Talks", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "Vote", article_ids: ["A2"], pass: false, reason: "summary: x", failed_fields: ["summary" as const] }] };
    expect(recallOnPlants(plants, report)).toEqual({ caught: 1, total: 1 });
    expect(recallOnPlants([{ ...plants[0]!, field: "headline" }], report)).toEqual({ caught: 0, total: 1 });
    expect(recallOnPlants([{ ...plants[0]!, storyIndex: 9 }], report)).toEqual({ caught: 0, total: 1 });
  });
  it("disagreements are the cells where two judges differ, ignoring cells only one judged", () => {
    const a = [{ story: 0, criterion: 1 as const, pass: true, reason: "" }, { story: 0, criterion: 2 as const, pass: true, reason: "" }, { story: 1, criterion: 1 as const, pass: true, reason: "" }];
    const b = [{ story: 0, criterion: 1 as const, pass: false, reason: "" }, { story: 0, criterion: 2 as const, pass: true, reason: "" }];
    expect(disagreements(a, b)).toEqual([{ story: 0, criterion: 1 }]);
  });
});
