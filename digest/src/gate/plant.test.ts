import { describe, expect, it } from "vitest";
import { plantDefects } from "./plant.js";

// Every field carries a number, so every plant is a wrong-number plant and the shape check always runs.
const draft = {
  must_know: [
    { headline: "Talks resume after 12 days", summary: "Officials said 3,000 attended.", why_it_matters: "The 2 sides differ.", sources: [{ article_id: "A1" }] },
    { headline: "Vote passes 58 to 40", summary: "Turnout was 58%.", why_it_matters: "A 9 point swing.", sources: [{ article_id: "A2" }] },
  ],
  should_know: [],
  preheader: "p",
};

describe("plantDefects", () => {
  it("is deterministic for a seed and changes exactly n distinct fields, leaving the input alone", () => {
    const a = plantDefects(draft, 7, 2);
    expect(plantDefects(draft, 7, 2).plants).toEqual(a.plants);
    expect(a.plants).toHaveLength(2);
    expect(new Set(a.plants.map((p) => `${p.storyIndex}:${p.field}`)).size).toBe(2);
    for (const p of a.plants) {
      expect(p.original).not.toBe(p.planted);
      expect(a.draft.must_know[p.storyIndex]![p.field]).toBe(p.planted);
      expect(draft.must_know[p.storyIndex]![p.field]).toBe(p.original);
    }
  });
  it("a wrong-number plant changes a number and nothing else in the field", () => {
    const { plants } = plantDefects(draft, 1, 3);
    for (const p of plants) {
      expect(p.kind).toBe("wrong-number");
      expect(p.planted.replace(/\d[\d,]*/g, "#")).toBe(p.original.replace(/\d[\d,]*/g, "#"));
    }
  });
  it("a field with no number gets an absent specific appended", () => {
    const noNumbers = { ...draft, must_know: [{ ...draft.must_know[0]!, headline: "Talks resume", summary: "Officials attended.", why_it_matters: "Sides differ." }] };
    const { plants } = plantDefects(noNumbers, 3, 1);
    expect(plants[0]!.kind).toBe("absent-specific");
    expect(plants[0]!.planted.startsWith(plants[0]!.original)).toBe(true);
  });
  it("refuses more plants than there are fields instead of looping", () => {
    expect(() => plantDefects(draft, 1, 7)).toThrow(/6 must_know fields/);
  });
});
