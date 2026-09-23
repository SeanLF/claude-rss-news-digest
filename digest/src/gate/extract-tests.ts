// promptfoo test generator for the fulltext fork: one test per page the fetch saved. The corpus
// directory is FULLTEXT_FORK_DIR (a fresh data/fulltext-fork-<stamp>/ from fulltext_fork_pages.py).
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { normalise, truncateAtSentence } from "../fulltext/extract.js";

interface Page { key: string; url: string; fetched: boolean; trafilatura?: string }

export default function tests() {
  const dir = process.env["FULLTEXT_FORK_DIR"];
  if (!dir) throw new Error("FULLTEXT_FORK_DIR is not set");
  const { max_chars: maxChars } = JSON.parse(readFileSync(join(dir, "config.json"), "utf8")) as { max_chars: number };
  const pages = readFileSync(join(dir, "pages.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Page);
  return pages
    .filter((p) => p.fetched && existsSync(join(dir, "html", `${p.key}.html`)))
    .map((p) => ({ description: p.key, vars: { key: p.key, url: p.url, html: join(dir, "html", `${p.key}.html`), maxChars, reference: truncateAtSentence(normalise(p.trafilatura ?? ""), maxChars) } }));
}
