import { readFileSync, writeFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { issueMarkdown } from "./markdown.js";
import { sameDocument } from "./parity/document.js";

// Host-only (bin/site-parity): every issue in the recorded clone, converted by the TypeScript side and
// compared with the Markdown the Rust server (htmd) served for it (bin/site-parity-record's corpus).
// The contract is the document (parity/document.ts); byte equality is reported beside it.
const DIR = process.env["SITE_PARITY_DIR"];

// The first differing line, trailing whitespace aside.
function firstDiff(a: string, b: string): string {
  const la = a.split("\n").map((l) => l.trimEnd());
  const lb = b.split("\n").map((l) => l.trimEnd());
  for (let i = 0; i < Math.max(la.length, lb.length); i++) if (la[i] !== lb[i]) return `line ${i + 1}\n  rust: ${JSON.stringify(la[i])}\n  ts:   ${JSON.stringify(lb[i])}`;
  return "";
}

describe.skipIf(!DIR)("issue Markdown against htmd, every issue in the clone", () => {
  it("renders the same document", () => {
    const db = new DatabaseSync(`${DIR}/legacy.db`, { readOnly: true });
    const rows = db.prepare("SELECT date, html FROM digests ORDER BY date").all() as { date: string; html: string }[];
    let bytes = 0;
    const differ: string[] = [];
    for (const r of rows) {
      const golden = readFileSync(`${DIR}/golden/corpus/${r.date}.md`, "utf8");
      const ours = issueMarkdown(r.html, "News Digest", r.date) ?? "";
      if (process.env["SITE_PARITY_DUMP"]) writeFileSync(`${process.env["SITE_PARITY_DUMP"]}/${r.date}.md`, ours);
      if (ours === golden) bytes++;
      else if (!sameDocument(golden, ours)) differ.push(`${r.date}: ${firstDiff(golden, ours)}`);
    }
    console.log(`markdown parity: ${rows.length - differ.length} of ${rows.length} render the same, ${bytes} byte-equal`);
    expect(differ.slice(0, 8)).toEqual([]);
    expect(rows.length).toBeGreaterThan(0);
  });
});
