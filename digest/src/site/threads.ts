import { cleanFact, cleanQuestions, whatsNew } from "../threads/text.js";
import type { SiteData, ThreadIndexData, ThreadSummaryData } from "./data.js";

// Thread pages and tools (circulation's thread.rs), over the pipeline's own reader-facing text rules
// (threads/text.ts), so the site and the email scrub article ids the same way.

export const OLDER_PAGE = 30;
const MAX_OLDER_PAGE = 100;
const MAX_MERGE_HOPS = 8;
// The "still watching" ledger shows the recent frontier, not the backlog.
const LEDGER_MAX = 6;

export interface ThreadSummary {
  id: number;
  label: string;
  status: string;
  updatedAt: string;
  updateCount: number;
  // The latest update's first clean "what's new" fact.
  summary: string;
}
export interface ThreadIndexPage {
  ongoing: ThreadSummary[];
  older: ThreadSummary[];
  olderTotal: number;
  nextBefore: { updatedAt: string; id: number } | null;
}
export interface ThreadEntry {
  day: string;
  issueDate: string | null;
  headline: string;
  facts: string[];
}
export interface ThreadDetail {
  label: string;
  status: string;
  // The survivor's id when the requested thread was merged into it.
  mergedInto: number | null;
  entries: ThreadEntry[];
  openQuestions: string[];
}

// The top three clean facts of an update; a fact that fails cleaning yields its place to the next.
export const factsFrom = (content: string | null): string[] => whatsNew(content).map(cleanFact).filter(Boolean).slice(0, 3);

const sourcesOf = (f: unknown): unknown[] => (f && typeof f === "object" && "sources" in f && Array.isArray(f.sources) ? (f.sources as unknown[]) : []);

// The ids one update cited: its recorded cited_ids (written before the faithfulness audit, so they still
// name the sources of dropped facts), else its surviving facts' own sources.
export function citedIds(content: string | null): string[] {
  if (!content) return [];
  let doc: unknown;
  try {
    doc = JSON.parse(content);
  } catch {
    return [];
  }
  if (!doc || typeof doc !== "object") return [];
  const d = doc as { cited_ids?: unknown; whats_new?: unknown };
  const listed: unknown[] = Array.isArray(d.cited_ids) ? (d.cited_ids as unknown[]) : Array.isArray(d.whats_new) ? (d.whats_new as unknown[]).flatMap(sourcesOf) : [];
  return listed.flatMap((s) => (typeof s === "string" && s.trim() ? [s.trim()] : []));
}

const summary = (t: ThreadSummaryData): ThreadSummary => ({
  id: t.id,
  label: t.label,
  status: t.status,
  updatedAt: t.updatedAt,
  updateCount: t.updateCount,
  summary: factsFrom(t.latestContent)[0] ?? "",
});

export async function threadIndex(data: SiteData, before: { updatedAt: string; id: number } | undefined, limit: number): Promise<ThreadIndexPage> {
  const lim = Math.min(Math.max(limit, 1), MAX_OLDER_PAGE);
  const raw: ThreadIndexData = await data.threadIndex(before, lim + 1);
  const hasMore = raw.older.length > lim;
  const older = raw.older.slice(0, lim).map(summary);
  const last = older.at(-1);
  return { ongoing: raw.ongoing.map(summary), older, olderTotal: raw.olderTotal, nextBefore: hasMore && last ? { updatedAt: last.updatedAt, id: last.id } : null };
}

// Follows a merge chain to its survivor; undefined for no such thread, a self-merge or a chain too long.
export async function threadDetail(data: SiteData, requested: number): Promise<ThreadDetail | undefined> {
  let id = requested;
  for (let hop = 0; ; hop++) {
    const next = await data.mergedInto(id);
    if (next === undefined) return undefined;
    if (next === null) break;
    if (next === id || hop >= MAX_MERGE_HOPS) {
      console.warn(JSON.stringify({ site: "thread", requested, warning: next === id ? "merged into itself" : `merge chain longer than ${MAX_MERGE_HOPS} hops` }));
      return undefined;
    }
    id = next;
  }
  const t = await data.thread(id);
  if (!t) return undefined;
  return {
    label: t.label,
    status: t.status,
    mergedInto: id === requested ? null : id,
    entries: t.installments.map((i) => ({ day: i.day, issueDate: i.issueDate, headline: i.story, facts: factsFrom(i.content) })),
    // Grounded on the ids the raising run cited: article ids are per-run labels.
    openQuestions: t.openQuestions.flatMap((q) => cleanQuestions([q.question], citedIds(q.raisedContent))).slice(0, LEDGER_MAX),
  };
}
