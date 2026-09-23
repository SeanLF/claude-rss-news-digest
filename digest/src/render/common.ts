import { readFileSync } from "node:fs";
import type { DatabaseSync } from "node:sqlite";

// The shape newsroom/src/render.py and render_email.py consume: selections after article ids are
// resolved and thread context is attached. Fields are optional because the Python reads them with
// .get and archived selections predate some of them.
export interface Source { article_id?: string; name?: string; url?: string; bias?: string; source_id?: string; original_title?: string; wire?: boolean; wire_agency?: string | null | undefined }
export interface ThreadContext { thread_id?: number; day?: number; delta?: string | null; url?: string | null }
export interface ReportingVaries { source?: string; angle?: string; bias?: string }
export interface Story { headline?: string; summary?: string; why_it_matters?: string; reporting_varies?: ReportingVaries[]; sources: Source[]; cluster_id?: string; thread?: ThreadContext | null }
export interface Selections { must_know: Story[]; should_know: Story[]; preheader?: string; not_covered_blurb?: string | null }
// The deployment's environment, as the Python reads it: DIGEST_NAME unset means "News Digest".
export interface RenderEnv { digestName?: string | undefined; digestDomain: string; archiveUrl: string; authorName: string; authorUrl: string }
export interface RenderAssets { template: string; styles: string; tokens: string }
export interface RenderInput { selections: Selections; now: Date; issueNo: number | null; env: RenderEnv; assets: RenderAssets }

export function envFrom(env: NodeJS.ProcessEnv): RenderEnv {
  return { digestName: env["DIGEST_NAME"], digestDomain: env["DIGEST_DOMAIN"] ?? "", archiveUrl: env["ARCHIVE_URL"] ?? "", authorName: env["AUTHOR_NAME"] ?? "", authorUrl: env["AUTHOR_URL"] ?? "" };
}
const read = (p: string) => readFileSync(p, "utf8");
export function loadAssets(dirs: { templates: string; design: string }): RenderAssets {
  return { template: read(`${dirs.templates}/digest-template.html`), styles: read(`${dirs.templates}/digest.css`), tokens: read(`${dirs.design}/tokens.css`) };
}

// Python's round() rounds halves to even; Math.round would put 2.5 minutes, or a 12.5% bias-bar
// cell, one higher than the Python does.
export function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d !== 0.5) return d < 0.5 ? f : f + 1;
  return f % 2 === 0 ? f : f + 1;
}
// Python slices and counts by code point, JavaScript by UTF-16 unit.
export const codePoints = (s: string, n: number) => Array.from(s).slice(0, n).join("");

export function slugify(text: string, maxLength = 60): string {
  const slug = text.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-+|-+$/g, "").slice(0, maxLength).replace(/-+$/, "");
  return slug || "story";
}
export function slugger(): (headline: string) => string {
  const used = new Set<string>();
  return (headline) => {
    const base = slugify(headline);
    let s = base;
    for (let n = 2; used.has(s); n++) s = `${base}-${n}`;
    used.add(s);
    return s;
  };
}

export const isSafeUrl = (url: string) => url.startsWith("http://") || url.startsWith("https://");
// urllib.parse.urlparse's path, params split off the last segment: a bare domain links to a homepage.
function urlPath(url: string): string {
  const rest = url.replace(/^[A-Za-z][A-Za-z0-9+.-]*:/, "");
  const path = (rest.startsWith("//") ? rest.slice(2).replace(/^[^/?#]*/, "") : rest).replace(/[?#].*$/s, "");
  const i = path.includes("/") ? path.indexOf(";", path.lastIndexOf("/")) : path.indexOf(";");
  return i < 0 ? path : path.slice(0, i);
}
const hasArticlePath = (url: string) => !["", "/"].includes(urlPath(url));

export function readingTime(selections: Selections, wordsPerMinute = 200): string {
  const parts: string[] = [];
  for (const tier of ["must_know", "should_know"] as const)
    for (const a of selections[tier]) {
      parts.push(a.headline ?? "", a.summary ?? "", a.why_it_matters ?? "");
      for (const rv of a.reporting_varies ?? []) parts.push(rv.angle ?? "");
    }
  const words = parts.join(" ").split(/\s+/).filter(Boolean).length;
  return `${Math.max(1, roundHalfEven(words / wordsPerMinute))} min read`;
}
export const storyCounts = (s: Selections) => `${s.must_know.length} must-know · ${s.should_know.length} should-know`;

export type Bucket = "l" | "c" | "r";
export const BUCKET_ORDER: Bucket[] = ["l", "c", "r"];
export const BUCKET_WORD: Record<Bucket, string> = { l: "left", c: "center", r: "right" };
const KNOWN_CENTER = new Set(["", "center", "centre", "lean-center", "lean-centre", "center-left", "center-right", "centre-left", "centre-right", "central", "mixed"]);
function biasBucket(bias: string): Bucket {
  const b = bias.trim().toLowerCase();
  if (["lean-left", "left", "far-left"].includes(b)) return "l";
  if (["lean-right", "right", "far-right"].includes(b)) return "r";
  if (!KNOWN_CENTER.has(b)) console.warn(JSON.stringify({ stage: "render", warning: "unmapped bias label bucketed as center", bias }));
  return "c";
}
export const AGENCY_LABELS: Record<string, string> = {
  afp: "AFP", "agence france-presse": "AFP", ap: "AP", "associated press": "AP", dpa: "dpa", efe: "EFE", "agencia efe": "EFE", ansa: "ANSA", pti: "PTI", "press trust of india": "PTI", ians: "IANS", upi: "UPI", "united press international": "UPI", "pa media": "PA Media", "press association": "PA Media", tass: "TASS",
};
// str.title(): each run of letters capitalised, the rest of the run lowered.
export const titleCase = (s: string) => s.replaceAll(/\p{L}+/gu, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());

export interface Outlet { name: string; bias: string; bucket: Bucket; urls: string[]; wireAgency: string | null | undefined }
// Sources grouped by outlet in input order, keeping only links a reader can open.
export function collectOutlets(story: Story): Outlet[] {
  const order: Outlet[] = [];
  const index = new Map<string, Outlet>();
  for (const src of story.sources) {
    const name = src.name ?? "";
    if (!name) continue;
    let entry = index.get(name);
    if (!entry) {
      entry = { name, bias: src.bias ?? "", bucket: biasBucket(src.bias ?? ""), urls: [], wireAgency: src.wire_agency };
      index.set(name, entry);
      order.push(entry);
    }
    const url = src.url ?? "";
    if (url && isSafeUrl(url) && hasArticlePath(url)) entry.urls.push(url);
  }
  const result = order.filter((o) => o.urls.length);
  if (order.length && !result.length) console.warn(JSON.stringify({ stage: "render", warning: "every source outlet dropped (no article-path URLs); the story ships with no source block", outlets: order.length }));
  return result;
}
export function bucketCounts(outlets: Outlet[]): Record<Bucket, number> {
  const counts: Record<Bucket, number> = { l: 0, c: 0, r: 0 };
  for (const o of outlets) counts[o.bucket]++;
  return counts;
}

// The light-mode design tokens: the plain :root blocks of tokens.css, the dark-mode media block
// removed first, so the email's colours are the web's.
function stripMediaBlock(css: string, keyword: string): string {
  const open = new RegExp(`@media\\s*\\([^)]*${keyword.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^)]*\\)\\s*\\{`);
  let out = "";
  let i = 0;
  while (i < css.length) {
    const m = open.exec(css.slice(i));
    if (!m) {
      out += css.slice(i);
      break;
    }
    out += css.slice(i, i + m.index);
    let j = i + m.index + m[0].length;
    for (let depth = 1; j < css.length && depth > 0; j++) depth += css[j] === "{" ? 1 : css[j] === "}" ? -1 : 0;
    i = j;
  }
  return out;
}
export function lightTokens(tokensCss: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  for (const block of stripMediaBlock(tokensCss, "prefers-color-scheme").matchAll(/:root\s*\{([^}]+)\}/g))
    for (const m of block[1]!.matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[m[1]!] = m[2]!.trim();
  return tokens;
}

const utc = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { ...opts, timeZone: "UTC" });
const pad = (n: number) => String(n).padStart(2, "0");
export function dates(now: Date) {
  const hm = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}`;
  return {
    long: `${utc({ weekday: "long" }).format(now)}, ${utc({ month: "long" }).format(now)} ${now.getUTCDate()}, ${now.getUTCFullYear()}`,
    iso: now.toISOString().slice(0, 10),
    filed: `${hm} UTC`,
    generated: `Generated at ${hm} UTC`,
  };
}

// db.get_issue_number: the edition's rank among stored digests, counting this one if it is not yet stored.
export function issueNumber(db: DatabaseSync, date: string): number {
  const { n } = db.prepare("SELECT COUNT(*) AS n FROM digests WHERE date <= ?").get(date) as { n: number };
  const stored = db.prepare("SELECT 1 FROM digests WHERE date = ? LIMIT 1").get(date);
  return stored ? n : n + 1;
}
