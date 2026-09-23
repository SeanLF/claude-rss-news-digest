import { describe, expect, it } from "vitest";
import { extract, truncateAtSentence, type Arm } from "./extract.js";

const body = Array.from({ length: 12 }, (_, i) => `<p>Paragraph ${i} reports that the ministry confirmed ${i * 7} new measures on Tuesday, according to officials who spoke at length.</p>`).join("");
const page = `<html><head><title>T</title></head><body>
<nav><a href="/">Home</a> <a href="/world">World</a> Subscribe to our newsletter</nav>
<article><h1>Ministry confirms measures</h1>${body}</article>
<footer>Copyright 2026 Example News. All rights reserved.</footer></body></html>`;

describe("truncateAtSentence", () => {
  it("leaves text at or under the cap alone", () => {
    expect(truncateAtSentence("One. Two.", 9)).toBe("One. Two.");
  });
  it("cuts at the last sentence end inside the window and marks the cut", () => {
    expect(truncateAtSentence("One. Two. Three.", 12)).toBe("One. Two.\n[truncated]");
  });
  it("hard-cuts a window with no sentence end", () => {
    expect(truncateAtSentence("abcdefghij", 4)).toBe("abcd\n[truncated]");
  });
  it("counts code points, as the Python does", () => {
    expect(truncateAtSentence("😀😀 a.", 5)).toBe("😀😀 a.");
  });
});

describe("extract", () => {
  it.each<Arm>(["defuddle", "readability", "dom-smoothie"])("%s keeps the article body and drops the chrome", async (arm) => {
    const text = await extract(arm, page, "https://example.com/a");
    expect(text).toContain("Paragraph 11 reports");
    expect(text).not.toContain("Subscribe to our newsletter");
    expect(text).not.toMatch(/https?:\/\//);
  });
  it.each<Arm>(["defuddle", "readability", "dom-smoothie"])("%s returns empty text for a page with no article", async (arm) => {
    expect((await extract(arm, "<html><body><nav>Home</nav></body></html>", "https://example.com/")).length).toBeLessThan(200);
  });
});
