import { z } from "zod";
import type { ActiveThread, ThreadStore } from "./store.js";

// threads.py's linker half, ported: which selected stories the run has, the linker's prompt, the
// validation of its answer, and the assignment that persists identity.

export interface StoryLabel { story: string; tier: "must_know" | "should_know"; article_ids: string[] }
export interface Assignment { thread_id: number; is_new: boolean; story: string; article_ids: string[] }
export interface LinkHealth { ok: boolean; proposed: number; validated: number }
export interface StoryTrace { story_index: number; label: string; article_ids: string[]; proposed_thread: number | null; refused: "unknown_thread" | "already_claimed" | null; outcome: "continued" | "new" }
export interface LinkTrace { linker_ok: boolean; proposed: number; validated: number; candidates: ActiveThread[]; stories: StoryTrace[] }

export interface Links { links: { story: number | null; thread: number | null }[] }

// The linker answers in free text, not a schema: on run 304, four schema-constrained samples all
// dropped a continuation that free text made 6 of 6 times (3 Python, 3 TypeScript).
// threads._as_index: a digit string is the same answer as the number (run 244 quoted every id);
// anything else, "NEW" and null included, is a new thread.
export function asIndex(v: unknown): number | null {
  if (typeof v === "number") return Number.isInteger(v) ? v : null;
  if (typeof v === "string" && /^\s*\d+\s*$/.test(v)) return Number(v.trim());
  return null;
}

// threads._parse_links: the {"links": [...]} object between the first "{" and the last "}", tolerant
// of fences and prose; throws when nothing usable is there, so the attempt is re-sampled.
export function parseLinks(text: string): Links {
  const s = text.indexOf("{");
  const e = text.lastIndexOf("}");
  let raw: unknown;
  try {
    raw = s >= 0 && e > s ? (JSON.parse(text.slice(s, e + 1)) as unknown) : undefined;
  } catch {
    raw = undefined;
  }
  const parsed = z.object({ links: z.array(z.unknown()) }).safeParse(raw);
  if (!parsed.success) throw new Error(`the linker returned no parseable links: ${JSON.stringify(text.slice(0, 300))}`);
  return {
    links: parsed.data.links.flatMap((ln) => {
      if (!ln || typeof ln !== "object") return [];
      const { story, thread } = ln as { story?: unknown; thread?: unknown };
      return [{ story: asIndex(story), thread: asIndex(thread) }];
    }),
  };
}

interface Cluster { story?: unknown; article_ids?: unknown }
interface Pick { article_ids?: unknown; cluster_index?: unknown }

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

// utils.cluster_for_articles: the cluster label holding the most of these DISTINCT ids; ties keep
// the earliest cited.
export function clusterForArticles(articleIds: unknown, owner: ReadonlyMap<string, string>): string | undefined {
  const counts = new Map<string, number>();
  for (const a of new Set(strings(articleIds))) {
    const s = owner.get(a);
    if (s) counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  let best: string | undefined;
  for (const [s, n] of counts) if (best === undefined || n > counts.get(best)!) best = s;
  return best;
}

// threads.selected_labels: each SELECT pick's cluster label, keyed on its article ids (the index is
// a model's count into hundreds of clusters and drifts); the index only for a pick with no ids, and
// a pick whose ids map to no cluster is skipped rather than mislabelled.
export function selectedLabels(clustersDoc: unknown, selectedDoc: unknown): StoryLabel[] {
  const clusters = ((clustersDoc as { clusters?: unknown })?.clusters ?? []) as Cluster[];
  const owner = new Map<string, string>();
  for (const c of clusters) {
    if (typeof c.story !== "string" || !c.story) continue;
    for (const a of strings(c.article_ids)) owner.set(a, c.story); // a later cluster wins, as in the Python's dict
  }
  const out: StoryLabel[] = [];
  for (const tier of ["must_know", "should_know"] as const) {
    const picks = ((selectedDoc as Record<string, unknown>)?.[tier] ?? []) as Pick[];
    for (const entry of Array.isArray(picks) ? picks : []) {
      const ids = Array.isArray(entry.article_ids) ? (entry.article_ids as unknown[]) : [];
      const idx = entry.cluster_index;
      const indexed = typeof idx === "number" && Number.isInteger(idx) && idx >= 0 && idx < clusters.length ? clusters[idx] : undefined;
      if (ids.length) {
        const story = clusterForArticles(ids, owner);
        if (story === undefined) {
          console.warn(JSON.stringify({ stage: "threads", warning: "selected entry cites articles in no cluster; skipped", tier, article_ids: ids.slice(0, 5) }));
          continue;
        }
        out.push({ story, tier, article_ids: ids as string[] });
      } else if (indexed) {
        out.push({ story: typeof indexed.story === "string" ? indexed.story : "", tier, article_ids: strings(indexed.article_ids) });
      }
    }
  }
  return out;
}

export function linkPrompt(active: ActiveThread[], labels: string[]): string {
  const threads = active.map((t) => `  [${t.thread_id}] ${(t.recent_labels.length ? t.recent_labels : [t.label]).join(" -> ")}`).join("\n");
  const today = labels.map((l, i) => `  (${i}) ${l}`).join("\n");
  return `ACTIVE THREADS:\n${threads}\n\nTODAY'S STORIES:\n${today}\n\nMap each today-story to a thread id or NEW.`;
}

// link_threads' validation: a link counts only for an in-range story and an offered thread id;
// the last valid link for a story wins, as in the Python's loop.
export function validateLinks(links: Links, active: ActiveThread[], n: number): { mapping: (number | null)[]; health: LinkHealth } {
  const valid = new Set(active.map((t) => t.thread_id));
  const mapping: (number | null)[] = Array.from({ length: n }, () => null);
  for (const ln of links.links) if (ln.story !== null && ln.story >= 0 && ln.story < n && ln.thread !== null && valid.has(ln.thread)) mapping[ln.story] = ln.thread;
  const proposed = links.links.filter((ln) => ln.thread !== null).length;
  const validated = mapping.filter((v) => v !== null).length;
  if (proposed > validated) console.error(JSON.stringify({ stage: "threads", warning: "linker proposals refused as invalid; those stories start new threads", proposed, validated, candidates: active.length }));
  return { mapping, health: { ok: true, proposed, validated } };
}

// resolve_threads after the linker has answered: continue each validly-linked thread at most once
// per run, start a new thread for everything else, and record why a proposal was refused. Writes
// through `store`; the caller owns the transaction.
export function assignThreads(store: ThreadStore, stories: StoryLabel[], runId: number, active: ActiveThread[], mapping: (number | null)[], health: LinkHealth): { assignments: Assignment[]; trace: LinkTrace } {
  const offered = new Set(active.map((t) => t.thread_id));
  const claimed = new Set<number>();
  const assignments: Assignment[] = [];
  const trace: LinkTrace = { linker_ok: health.ok, proposed: health.proposed, validated: health.validated, candidates: active, stories: [] };
  stories.forEach((st, i) => {
    const tid = i < mapping.length ? (mapping[i] ?? null) : null;
    let refused: StoryTrace["refused"] = null;
    if (tid !== null) {
      if (!offered.has(tid)) refused = "unknown_thread";
      else if (claimed.has(tid)) {
        refused = "already_claimed";
        console.warn(JSON.stringify({ stage: "threads", warning: "thread already claimed this run; story starts a new thread", thread_id: tid, story: st.story.slice(0, 80) }));
      }
    }
    const continued = tid !== null && refused === null;
    trace.stories.push({ story_index: i, label: st.story, article_ids: [...st.article_ids], proposed_thread: tid, refused, outcome: continued ? "continued" : "new" });
    if (continued) {
      claimed.add(tid);
      store.touchThread(tid, st.story, runId);
      store.recordInstallment(tid, runId, st.story, false);
      assignments.push({ thread_id: tid, is_new: false, story: st.story, article_ids: [...st.article_ids] });
    } else {
      const id = store.createThread(st.story, runId);
      store.recordInstallment(id, runId, st.story, true);
      assignments.push({ thread_id: id, is_new: true, story: st.story, article_ids: [...st.article_ids] });
    }
  });
  return { assignments, trace };
}
