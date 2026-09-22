import { describe, expect, it } from "vitest";
import { assertNoUrls, parseArticleId } from "./ids.js";

describe("article ids", () => {
  it("accepts A1 and A42", () => {
    expect(parseArticleId("A1")).toBe("A1");
    expect(parseArticleId("A42")).toBe("A42");
  });
  it("rejects anything else", () => {
    for (const bad of ["a1", "A", "A-1", "1", "A1 ", "https://x"]) expect(() => parseArticleId(bad)).toThrow();
  });
  it("assertNoUrls throws on a URL and passes on a source id", () => {
    expect(() => assertNoUrls("see https://example.com/x")).toThrow(/URL/);
    expect(() => assertNoUrls("reuters,Reuters,centre")).not.toThrow();
  });
});
