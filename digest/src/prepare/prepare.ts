import { stringify } from "csv-stringify/sync";
import { TfidfMatcher } from "./dedup.js";
import { scrubUrls } from "../contracts/ids.js";
import { canonicalUrl, escapeHtml, estimateTokens, isSafeUrl, stripHtml, truncate } from "./text.js";
import { wireAgency, wireFromDateline } from "./wire.js";

export const MAX_TITLE_LENGTH = 500;
export const MAX_SUMMARY_LENGTH = 200;
export const MAX_TOKENS_PER_FILE = 10_000;
export const DEDUP_SIMILARITY_THRESHOLD = 0.8;
export const ARTICLE_HEADER = ["article_id", "source_id", "title", "published", "summary"] as const;

export interface Source { id: string; name: string; bias: string; factuality: string; perspective: string }
// author is optional: the live fetch has it, the archive (articles) never stored it.
export interface Fetched { title: string; url: string; published: string | null; summary: string | null; author?: string | null }
export interface IndexEntry { url: string; source_id: string; bias: string; original_title: string; name: string; wire: boolean; wire_agency: string | null }
export interface Prepared {
  files: { name: string; rows: string[][] }[];
  index: Record<string, IndexEntry>;
  filtered: { title: string; source_id: string; matched: string; similarity: number }[];
  urlDuplicates: number;
}

// prepare.prepare_claude_input's article pass, pure: in source order, drop unsafe and repeated URLs,
// escape and cap titles and summaries, drop a title too close to a recently shown one, number the
// survivors A1.., and split the rows into files of about MAX_TOKENS_PER_FILE tokens.
// scrubLinks (the default) replaces links inside titles and summaries with a token: the Python let Hacker
// News summaries carry "Article URL: https://..." to every model stage. Parity with the archive is
// checked with it off.
export function prepareArticles(sources: Source[], fetched: Map<string, Fetched[]>, recentTitles: string[], opts: { scrubLinks?: boolean } = {}): Prepared {
  const scrub = opts.scrubLinks ?? true ? scrubUrls : (t: string) => t;
  const matcher = recentTitles.length ? new TfidfMatcher(recentTitles) : undefined;
  const seen = new Set<string>();
  const rows: string[][] = [];
  const index: Record<string, IndexEntry> = {};
  const filtered: Prepared["filtered"] = [];
  let urlDuplicates = 0;
  for (const source of sources)
    for (const a of fetched.get(source.id) ?? []) {
      const url = truncate(a.url ?? "", 2000);
      if (!isSafeUrl(url)) continue;
      const canonical = canonicalUrl(url);
      if (seen.has(canonical)) {
        urlDuplicates++;
        continue;
      }
      seen.add(canonical);
      // Links are scrubbed before the cap, so a cap never leaves half a URL behind.
      const title = truncate(escapeHtml(scrub(stripHtml(a.title ?? ""))), MAX_TITLE_LENGTH);
      const summary = truncate(escapeHtml(scrub(stripHtml(a.summary ?? ""))), MAX_SUMMARY_LENGTH);
      if (matcher && title) {
        const m = matcher.findMostSimilar(title);
        if (m.score >= DEDUP_SIMILARITY_THRESHOLD) {
          filtered.push({ title, source_id: source.id, matched: m.headline ?? "", similarity: m.score });
          continue;
        }
      }
      const id = `A${rows.length + 1}`;
      const wire = source.perspective === "wire_service";
      index[id] = { url, source_id: source.id, bias: source.bias, original_title: title, name: source.name, wire, wire_agency: wireAgency(a.author) ?? wireFromDateline(summary) ?? (wire ? wireAgency(source.name) : null) };
      rows.push([id, source.id, title, a.published ?? "", summary]);
    }
  const files: Prepared["files"] = [];
  let current: string[][] = [];
  let tokens = 0;
  for (const row of rows) {
    const t = estimateTokens(row.join(","));
    if (tokens + t > MAX_TOKENS_PER_FILE && current.length) {
      files.push({ name: `articles_${files.length + 1}.csv`, rows: current });
      current = [];
      tokens = 0;
    }
    current.push(row);
    tokens += t;
  }
  if (current.length) files.push({ name: `articles_${files.length + 1}.csv`, rows: current });
  return { files, index, filtered, urlDuplicates };
}

// Python's csv.writer, minimal quoting, with the LF line ends the archive holds.
export const toCsv = (header: readonly string[], rows: string[][]): string => stringify([[...header], ...rows], { record_delimiter: "\n" });
