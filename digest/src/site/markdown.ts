import type { IndexMeta } from "./data.js";
import type { IssueRow } from "./archive.js";

// The Markdown representations (circulation's markdown.rs): Accept negotiation, an issue's stored
// Markdown under the site's title line, and the archive index as Markdown.

export type Negotiated = "html" | "markdown" | "not-acceptable";

const EPS = 1e-4;

// HTML versus Markdown from an Accept header: q-values compared, never substring-matched; a tie goes to
// Markdown only when text/markdown is named outright (a coding agent), never on a wildcard (a
// browser); 406 when neither is acceptable; no header is HTML.
export function negotiate(accept: string | null | undefined): Negotiated {
  if (!accept?.trim()) return "html";
  let html: number | undefined;
  let md: number | undefined;
  let textWild: number | undefined;
  let anyWild: number | undefined;
  for (const part of accept.split(",")) {
    const [mediaRaw, ...params] = part.split(";").map((s) => s.trim());
    const media = mediaRaw?.toLowerCase();
    if (!media) continue;
    let q = 1;
    for (const p of params) {
      if (p.startsWith("q=")) {
        // A present q overrides 1; out of range clamps; an unparseable one is 0, never the strongest.
        const v = Number.parseFloat(p.slice(2).trim());
        q = Number.isFinite(v) && /^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/.test(p.slice(2)) ? Math.min(Math.max(v, 0), 1) : 0;
      }
    }
    const max = (cur: number | undefined) => (cur === undefined || q > cur ? q : cur);
    if (media === "text/html") html = max(html);
    else if (media === "text/markdown") md = max(md);
    else if (media === "text/*") textWild = max(textWild);
    else if (media === "*/*") anyWild = max(anyWild);
  }
  const best = (explicit: number | undefined) => {
    const vals = [explicit, textWild, anyWild].filter((v): v is number => v !== undefined);
    return vals.length ? Math.max(...vals) : undefined;
  };
  const h = best(html);
  const m = best(md);
  if (h === undefined && m === undefined) return "not-acceptable";
  if (m === undefined) return "html";
  if (h === undefined) return "markdown";
  if (m > h + EPS) return "markdown";
  if (h > m + EPS) return "html";
  return md !== undefined && Math.abs(md - m) < EPS ? "markdown" : "html";
}

// An issue's stored Markdown body as a standalone document. The title is the site's, so the stored
// body never carries a deployment's name.
export const issueMarkdown = (digestName: string, date: string, body: string): string => `# ${digestName} — ${date}\n\n${body}\n`;

const abs = (base: string, path: string): string => (base ? `${base.replace(/\/+$/, "")}${path}` : path);

export function indexMarkdown(digestName: string, meta: IndexMeta, issues: IssueRow[], base: string): string {
  let out = `# ${digestName}\n\n> An automated daily news briefing: geopolitics, tech, and privacy, from sources across the political spectrum, each labelled by bias and factuality. Curated and written by Claude, fact-checked against its sources; no human edits any issue.\n\n`;
  if (meta.firstDate && meta.newestDate) {
    out += `${meta.total} issue${meta.total === 1 ? "" : "s"} from ${meta.firstDate} to ${meta.newestDate} · ${meta.totalStories} stor${meta.totalStories === 1 ? "y" : "ies"}.\n\n`;
  }
  out += "## Issues\n\n";
  for (const r of issues) {
    const url = abs(base, `/issues/${r.date}.md`);
    const pre = r.preheader.trim();
    out += pre ? `- [${r.date}](${url}): ${pre} (${r.sourceCount} sources)\n` : `- [${r.date}](${url})\n`;
  }
  return out;
}

export const markdownLinkTag = (url: string): string => `<link rel="alternate" type="text/markdown" href="${url}">`;
export const markdownLinkHeader = (url: string): string => `<${url}>; rel="alternate"; type="text/markdown"`;
export const htmlLinkHeader = (url: string): string => `<${url}>; rel="alternate"; type="text/html"`;
// For the reader who pastes the page's URL into a chat assistant: hidden from sight and from screen readers.
export const hiddenPointer = (url: string): string =>
  `<div aria-hidden="true" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;">A Markdown version of this page is available at ${url}.</div>`;
