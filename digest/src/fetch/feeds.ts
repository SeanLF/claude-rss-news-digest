import { parseFeed } from "feedsmith";
import type { Fetched } from "../prepare/prepare.js";

// sources.json entry; the catalogue keeps parked sources so past issues keep their attribution.
export interface CatalogueSource { id: string; name: string; url: string; bias: string; factuality: string; perspective: string; active?: boolean; inactive_reason?: string }

// feeds.load_catalogue + load_sources: validate every entry, return the ones to fetch.
export function activeSources(catalogue: unknown): CatalogueSource[] {
  if (!Array.isArray(catalogue)) throw new Error("sources.json is not a list");
  return (catalogue as CatalogueSource[]).filter((s, i) => {
    for (const k of ["id", "name", "url", "bias", "factuality", "perspective"] as const) if (typeof s[k] !== "string") throw new Error(`sources.json[${i}] missing ${k}`);
    if (!/^https?:\/\//.test(s.url)) throw new Error(`sources.json[${i}] invalid URL: ${s.url}`);
    if (!/^[a-z0-9_]+$/.test(s.id)) throw new Error(`sources.json[${i}] invalid id '${s.id}'`);
    if (s.active !== undefined && typeof s.active !== "boolean") throw new Error(`sources.json[${i}] '${s.id}' has a non-boolean 'active'`);
    if (s.active === false && !s.inactive_reason) throw new Error(`sources.json[${i}] '${s.id}' is inactive but gives no inactive_reason`);
    return s.active !== false;
  });
}

// Python's datetime(...).isoformat() in UTC: "2026-09-18T09:43:43+00:00".
export function isoUtc(value: string | undefined): string | null {
  if (!value) return null;
  const t = Date.parse(value);
  if (Number.isNaN(t)) return value; // feedparser falls back to the raw string when it cannot parse
  return new Date(t).toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

const text = (v: unknown): string => (typeof v === "string" ? v : v && typeof v === "object" && "value" in v && typeof v.value === "string" ? v.value : "");
const cap = (s: string, n: number) => Array.from(s).slice(0, n).join("");

// feeds.fetch_source's per-entry mapping over any format feedsmith reads (RSS, Atom, RDF, JSON Feed):
// title stripped, link, published as UTC isoformat, summary raw and capped at 500, author capped at 200.
// Entries without a title or a link are dropped, as the Python drops them.
export function parseArticles(body: string): Fetched[] {
  const { format, feed } = parseFeed(body);
  const items: Record<string, unknown>[] = (format === "atom" ? (feed as { entries?: unknown[] }).entries : (feed as { items?: unknown[] }).items) as Record<string, unknown>[] ?? [];
  const out: Fetched[] = [];
  for (const e of items) {
    let title = "", url = "", published: string | undefined, summary = "", author = "";
    if (format === "atom") {
      title = text(e["title"]);
      const links = (e["links"] as { href?: string; rel?: string }[] | undefined) ?? [];
      url = (links.find((l) => !l.rel || l.rel === "alternate") ?? links[0])?.href ?? "";
      published = (e["published"] as string | undefined) ?? (e["updated"] as string | undefined);
      summary = text(e["summary"]) || text(e["content"]);
      author = ((e["authors"] as { name?: string }[] | undefined) ?? [])[0]?.name ?? "";
    } else if (format === "json") {
      title = text(e["title"]);
      url = text(e["url"]);
      published = (e["date_published"] as string | undefined) ?? (e["date_modified"] as string | undefined);
      summary = text(e["summary"]) || text(e["content_html"]) || text(e["content_text"]);
      author = ((e["authors"] as { name?: string }[] | undefined) ?? [])[0]?.name ?? "";
    } else {
      title = text(e["title"]);
      url = text(e["link"]);
      published = (e["pubDate"] as string | undefined) ?? ((e["dc"] as { dates?: string[]; date?: string } | undefined)?.dates?.[0] ?? (e["dc"] as { date?: string } | undefined)?.date);
      summary = text(e["description"]) || text((e["content"] as { encoded?: string } | undefined)?.encoded);
      author = text(e["author"]) || ((e["dc"] as { creators?: string[]; creator?: string } | undefined)?.creators?.[0] ?? (e["dc"] as { creator?: string } | undefined)?.creator ?? "");
    }
    title = title.trim();
    if (!title || !url) continue;
    out.push({ title, url, published: isoUtc(published), summary: cap(summary, 500), author: cap(author.trim(), 200) });
  }
  return out;
}

// feeds.fetch_feeds' age filter: keep an entry published after the last completed run, or undated.
export const newerThan = (articles: Fetched[], lastRun: string | null): Fetched[] =>
  lastRun ? articles.filter((a) => !a.published || Number.isNaN(Date.parse(a.published)) || Date.parse(a.published) > Date.parse(`${lastRun.replace(" ", "T")}Z`)) : articles;
