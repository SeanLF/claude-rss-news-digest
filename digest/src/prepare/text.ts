import { decodeHTML } from "entities";

// render.strip_html: drop tags, decode entities (HTML5, as Python's html.unescape), collapse whitespace.
export function stripHtml(text: string): string {
  return decodeHTML(text.replace(/<[^>]+>/g, "")).replace(/\s+/g, " ").trim();
}

// Python's html.escape(quote=True).
export function escapeHtml(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#x27;");
}

// A code-point prefix, as Python's s[:n].
export const truncate = (text: string, n: number): string => Array.from(text).slice(0, n).join("");

export const isSafeUrl = (url: string): boolean => url.startsWith("http://") || url.startsWith("https://");

// prepare._canonical_url: lowercase scheme and host, drop the fragment, trim one trailing slash run
// from the path; path and query verbatim. Feeds that list one page several times collapse; two
// different articles never do.
export function canonicalUrl(url: string): string {
  const s = url.trim();
  const m = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$/.exec(s);
  if (!m) return s;
  const [, scheme, host, path, query] = m;
  return `${scheme!.toLowerCase()}://${host!.toLowerCase()}${path!.replace(/\/+$/, "")}${query ?? ""}`;
}

// render.estimate_tokens: ~4 characters a token.
export const estimateTokens = (text: string): number => Math.floor(Array.from(text).length / 4); // code points, as Python's len()
