import { describe, expect, it } from "vitest";
import { cosine, tfidf, tokenize } from "./tfidf.js";

describe("tfidf, sklearn defaults", () => {
  it("tokenizes like `\\b\\w\\w+\\b`, lowercased", () => {
    expect(tokenize("U.S. Fed hikes: 25bp, again-and-again")).toEqual(["fed", "hikes", "25bp", "again", "and", "again"]);
  });
  it("matches sklearn's smooth idf and l2 norm on a hand-computed corpus", () => {
    // docs: "a b", "a c"; n=2; df(a)=2, df(b)=1, df(c)=1 → idf(a)=ln(3/3)+1=1, idf(b)=ln(3/2)+1
    const [d0, d1] = tfidf(["aa bb", "aa cc"]);
    const idfB = Math.log(3 / 2) + 1;
    const norm = Math.sqrt(1 + idfB * idfB);
    expect(d0!.get("aa")).toBeCloseTo(1 / norm, 10);
    expect(d0!.get("bb")).toBeCloseTo(idfB / norm, 10);
    expect(cosine(d0!, d1!)).toBeCloseTo((1 / norm) * (1 / norm), 10);
    expect(cosine(d0!, d0!)).toBeCloseTo(1, 10);
  });
});
