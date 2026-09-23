import { stripArticleIds } from "../contracts/leaks.js";

// threads.py's reader-facing text rules, ported. Every string here is model-authored JSON, so the
// container types are checked as strictly as the element types: a scalar `sources` ("A3" rather
// than ["A3"]) must not iterate as ["A", "3"], and circulation's Rust mirror renders the same rows.

export interface Fact { fact?: unknown; sources?: unknown }

const tidy = (text: string): string => text.replace(/\s{2,}/g, " ").trim();
const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Python's \b on a str pattern is Unicode-aware; JavaScript's is ASCII-only without these lookarounds.
const bounded = (id: string): RegExp => new RegExp(`(?<![\\p{L}\\p{N}_])${escape(id)}(?![\\p{L}\\p{N}_])`, "u");

// Trimmed, non-blank ids from a model-authored sources list; anything but a list is no ids.
export function cited(sources: unknown): string[] {
  if (!Array.isArray(sources)) return [];
  return sources.flatMap((s) => (typeof s === "string" && s.trim() ? [s.trim()] : []));
}

// Delimited ids stripped in place; a bare id the prose's own sources cite can only be detected, so
// the prose is dropped ("") instead of shipping "according to A238".
export function scrubProse(raw: unknown, sources: unknown): string {
  const text = typeof raw === "string" ? tidy(stripArticleIds(raw)) : "";
  if (!text) return "";
  return cited(sources).some((s) => bounded(s).test(text)) ? "" : text;
}

export function cleanFact(fact: unknown): string {
  if (!fact || typeof fact !== "object" || Array.isArray(fact)) return "";
  const f = fact as Fact;
  return scrubProse(f.fact, f.sources);
}

// Open questions fit to render, grounded on the ids ITS OWN RUN cited (ids are per-run labels).
export function cleanQuestions(questions: unknown, citedIds: unknown): string[] {
  if (!Array.isArray(questions)) return [];
  return questions.flatMap((q) => (typeof q === "string" && (q = scrubProse(q, citedIds)) ? [q as string] : []));
}

// The delta: the top-N clean whats_new facts joined as prose; a fact that fails cleaning yields its
// place to the next-ranked one.
export function deltaFromFacts(facts: unknown[], topN = 3): string {
  const out: string[] = [];
  for (const f of facts) {
    if (out.length >= topN) break;
    const t = cleanFact(f);
    if (t) out.push(t);
  }
  return out.join(" ");
}

// A stored installment's whats_new facts, [] when missing or corrupt.
export function whatsNew(content: string | null | undefined): unknown[] {
  if (!content) return [];
  try {
    const doc = JSON.parse(content) as unknown;
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) return [];
    const w = (doc as { whats_new?: unknown }).whats_new;
    return Array.isArray(w) ? w : [];
  } catch {
    return [];
  }
}

export function slugify(label: string, maxLen = 60): string {
  const slug = (label || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return Array.from(slug).slice(0, maxLen).join("") || "thread";
}
