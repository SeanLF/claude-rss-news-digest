import { describe, expect, it } from "vitest";
import { assertNoUrls, parseArticleId, scrubUrls } from "./ids.js";

describe("article ids", () => {
  it("accepts A1 and A42", () => {
    expect(parseArticleId("A1")).toBe("A1");
    expect(parseArticleId("A42")).toBe("A42");
  });
  it("rejects anything else", () => {
    for (const bad of ["a1", "A", "A-1", "1", "A1 ", "https://x"]) expect(() => parseArticleId(bad)).toThrow();
  });
  it("scrubUrls replaces every link form with a token", () => {
    expect(scrubUrls("see https://a.com/x, //b.org/y and http://10.0.0.1/z; 3/4 stays")).toBe("see [link], [link] and [link]; 3/4 stays");
  });
  it("assertNoUrls throws on a URL and passes on a source id", () => {
    expect(() => assertNoUrls("see https://example.com/x")).toThrow(/URL/);
    expect(() => assertNoUrls("//cdn.example.com/x.png")).toThrow(/URL/);
    expect(() => assertNoUrls("http://192.168.1.1/x")).toThrow(/URL/);
    expect(() => assertNoUrls("reuters,Reuters,centre")).not.toThrow();
    expect(() => assertNoUrls("a // comment and 3/4 of a path/segment")).not.toThrow();
  });
});
