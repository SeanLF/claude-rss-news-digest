import { describe, expect, it } from "vitest";
import { itemIds, normHeadline, resultMatches } from "./match.js";

describe("coherence matching", () => {
  it("normalises quotes, dashes, spacing, trailing punctuation and case", () => {
    expect(normHeadline("  Iran’s “deal” — agreed.  ")).toBe(normHeadline("iran's \"deal\" - AGREED"));
  });
  it("matches by the cited id set first, then by headline", () => {
    const ids = itemIds([{ article_id: "A1" }, { article_id: "A2" }]);
    expect(resultMatches({ article_ids: ["A2", "A1"], headline: "x" }, ids, "y")).toBe(true);
    expect(resultMatches({ article_ids: ["A1"], headline: "y" }, ids, "y")).toBe(true);
    expect(resultMatches({ article_ids: ["A1"], headline: "z" }, ids, "y")).toBe(false);
  });
});
