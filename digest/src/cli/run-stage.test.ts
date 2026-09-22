import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildInvocation, inlineCorpus } from "./run-stage.js";

describe("run-stage CLI", () => {
  it("builds the invocation from flags", () => {
    const inv = buildInvocation(["--agent", "/a/coherence.md", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "coherence"]);
    expect(inv).toEqual({ agentPath: "/a/coherence.md", inputDir: "/in", today: "2026-09-21", schema: "coherence", inlineCorpus: false });
  });
  it("refuses an unknown schema name and a missing flag", () => {
    expect(() => buildInvocation(["--agent", "/a", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "nope"])).toThrow(/schema/);
    expect(() => buildInvocation(["--agent", "/a"])).toThrow(/input-dir/);
  });
  it("inlines the draft, the article CSVs and the fulltext, in name order, and nothing else", () => {
    const d = mkdtempSync(join(tmpdir(), "corpus-"));
    writeFileSync(join(d, "articles_2.csv"), "two");
    writeFileSync(join(d, "articles_1.csv"), "one");
    writeFileSync(join(d, "draft_selections.json"), "{}");
    writeFileSync(join(d, "coherence_report.json"), "{}");
    const c = inlineCorpus(d);
    expect(c.indexOf("## articles_1.csv")).toBeLessThan(c.indexOf("## articles_2.csv"));
    expect(c).toContain("## draft_selections.json");
    expect(c).not.toContain("coherence_report");
  });
  it("refuses a corpus that carries a URL", () => {
    const d = mkdtempSync(join(tmpdir(), "corpus-"));
    writeFileSync(join(d, "articles_1.csv"), "article_id,title,url\nA1,x,https://example.com/a\n");
    expect(() => inlineCorpus(d)).toThrow(/URL/);
  });
});
