import { describe, expect, it } from "vitest";
import judgePrompt, { stripUrls } from "./judge-prompt.js";

describe("judge prompt", () => {
  it("strips every href and bare URL from the digest", () => {
    expect(stripUrls('<a href="https://x.com/a">Reuters</a> see http://n.test/p, <img src=\'//i.test/a.png\'>')).toBe("<a>Reuters</a> see [link], <img>");
  });
  it("carries the rubric and a URL-free digest", () => {
    const p = judgePrompt({ vars: { digest: '<h2>Talks</h2><a href="https://x.com">src</a>' } });
    expect(p).toContain("Supported.");
    expect(p).toContain("<h2>Talks</h2><a>src</a>");
    expect(p).not.toMatch(/https?:/);
  });
});
