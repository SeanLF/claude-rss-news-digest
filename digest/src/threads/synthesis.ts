import { z } from "zod";
import type { ThreadStore } from "./store.js";
import { cited, cleanQuestions } from "./text.js";

// thread_synthesis.py, ported: late binding, the synthesis and audit prompts, the audit's answer
// check, and the persisted installment.

export interface Art { title: string; summary: string }
export type Arts = ReadonlyMap<string, Art>;

// How much of each article summary the synthesis and audit prompts carry. Code points, as the
// Python slices.
export const SUMMARY_CHARS = 400;
const head = (s: string, n = SUMMARY_CHARS): string => {
  const cps = Array.from(s);
  return cps.length <= n ? s : cps.slice(0, n).join("");
};

// Late binding: widen a thread's seed articles to their entity-similar neighbourhood across the
// run, with hub entities (Trump, US) dropped so they cannot fuse unrelated stories.
const ENTITY = /[A-Z][A-Za-z'&.-]+(?:\s+[A-Z][A-Za-z'&.-]+)*/g;
const LB_STOP = new Set(["the", "a", "an", "this", "that", "these", "those", "it", "its", "their", "his", "her", "our", "your", "new", "but", "and", "for"]);
const stripEnds = (w: string): string => w.replace(/^[.'&]+|[.'&]+$/g, "");

export function articleSignature(art: Art): Set<string> {
  const sig = new Set<string>();
  for (const ent of `${art.title} ${head(art.summary)}`.match(ENTITY) ?? [])
    for (const raw of ent.toLowerCase().split(/[\s-]+/)) {
      const w = stripEnds(raw);
      if (Array.from(w).length >= 3 && !LB_STOP.has(w)) sig.add(w);
    }
  return sig;
}

const jaccard = (a: Set<string>, b: Set<string>): number => {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  const union = a.size + b.size - inter;
  return union ? inter / union : 0;
};

function hubEntities(sigs: Map<string, Set<string>>, maxDf: number): Set<string> {
  const counts = new Map<string, number>();
  for (const sig of sigs.values()) for (const t of sig) counts.set(t, (counts.get(t) ?? 0) + 1);
  return new Set([...counts].filter(([, c]) => c / sigs.size > maxDf).map(([t]) => t));
}

export function expandNeighbourhood(seedIds: string[], arts: Arts, threshold: number, maxExtra: number, hubMaxDf = 0.12): string[] {
  const sigs = new Map([...arts].map(([id, a]) => [id, articleSignature(a)]));
  // IDF hub-stripping only bites at run scale; on tiny sets every entity looks like a hub.
  const hubs = sigs.size >= 30 ? hubEntities(sigs, hubMaxDf) : new Set<string>();
  const disc = new Map([...sigs].map(([id, s]) => [id, new Set([...s].filter((t) => !hubs.has(t)))]));
  const seed = seedIds.filter((a) => arts.has(a));
  const seedSigs = seed.map((a) => disc.get(a)!).filter((s) => s.size > 0);
  if (!seedSigs.length) return [...seedIds];
  const seedSet = new Set(seed);
  const scored: [number, string][] = [];
  for (const id of arts.keys()) {
    if (seedSet.has(id)) continue;
    const best = Math.max(0, ...seedSigs.map((s) => jaccard(disc.get(id)!, s)));
    if (best >= threshold) scored.push([best, id]);
  }
  // Python sorts (score, id) tuples in reverse: ties go to the larger id string.
  scored.sort((x, y) => (y[0] - x[0]) || (y[1] < x[1] ? -1 : y[1] > x[1] ? 1 : 0));
  return [...seedIds, ...scored.slice(0, maxExtra).map(([, id]) => id)];
}

export function bundle(articleIds: string[], arts: Arts): string {
  return articleIds.flatMap((a) => {
    const art = arts.get(a);
    return art ? [`${a}: ${art.title}\n   ${head(art.summary)}`] : [];
  }).join("\n\n");
}

export function synthesisPrompt(recentUpdates: string[], openQuestions: string[], articleIds: string[], arts: Arts): string {
  const updates = recentUpdates.map((u) => `- ${u}`).join("\n") || "(nothing yet -- this is the thread's first tracked day)";
  const questions = openQuestions.map((q) => `- ${q}`).join("\n") || "(none yet)";
  return `RECENT UPDATES:\n${updates}\nOPEN QUESTIONS:\n${questions}\n\nTODAY'S SOURCE ARTICLES:\n${bundle(articleIds, arts)}`;
}

const WhatsNew = z.object({ fact: z.string(), sources: z.array(z.string()) });
export const InstallmentSchema = z.object({
  whats_new: z.array(WhatsNew),
  resolved: z.array(z.object({ question: z.string(), how: z.string() })),
  new_questions: z.array(z.string()),
  still_open: z.array(z.string()),
});
export type Installment = z.infer<typeof InstallmentSchema>;

export const VerdictsSchema = z.object({ verdicts: z.array(z.object({ id: z.number().int(), supported: z.boolean(), issue: z.string().optional() })) });
export type Verdicts = z.infer<typeof VerdictsSchema>;

export function auditPrompt(whatsNew: Installment["whats_new"], arts: Arts): string {
  return whatsNew
    .map((f, i) => {
      const srcs = f.sources.flatMap((s) => {
        const a = arts.get(s);
        return a ? [`  [${s}] ${a.title}. ${head(a.summary)}`] : [];
      }).join("\n");
      return `CLAIM ${i + 1}: ${f.fact}\nCITED SOURCE(S):\n${srcs || "  (none cited)"}`;
    })
    .join("\n\n");
}

export const auditReask = (problem: string, n: number): string =>
  `\n\nIMPORTANT: an earlier attempt at these exact claims came back unusable (${problem}). Return EXACTLY ${n} verdicts, ids 1 through ${n}, one per CLAIM above, each carrying "supported": true or false. Output ONE JSON object and nothing else -- no prose, no second attempt inside the same reply.`;

// _answer_for: the verdicts answer the claim list only if there are exactly n of them with ids
// exactly 1..n. Returns the supported flag per claim, or a description of the mismatch.
export function answerFor(v: Verdicts, n: number): { supported: boolean[] } | { problem: string } {
  const ids = new Map<number, boolean>();
  for (const x of v.verdicts) ids.set(x.id, x.supported);
  const complete = v.verdicts.length === n && ids.size === n && [...ids.keys()].every((id) => id >= 1 && id <= n);
  if (complete) return { supported: Array.from({ length: n }, (_, i) => ids.get(i + 1)!) };
  const missing = Array.from({ length: n }, (_, i) => i + 1).filter((i) => !ids.has(i));
  return { problem: `verdicts missing/misaligned for claim(s) ${missing.length ? `[${missing.join(", ")}]` : "none"} (${v.verdicts.length} element(s), ids [${[...ids.keys()].toSorted((a, b) => a - b).join(", ")}])` };
}

// apply_installment: drop the unsupported facts, resolve the carried questions today answers, raise
// the new ones, and store the verified installment. The caller owns the transaction.
export function applyInstallment(store: ThreadStore, threadId: number, openNow: string[], installment: Installment, supported: boolean[], runId: number): Installment & { cited_ids: string[] } {
  const kept = installment.whats_new.filter((_, i) => supported[i] === true);
  // PRE-audit citations: the grounding scope for this run's questions (a dropped fact's ids too).
  const citedIds = [...new Set(installment.whats_new.flatMap((f) => cited(f.sources)))].toSorted();
  const verified = { ...installment, whats_new: kept, cited_ids: citedIds };
  const open = new Set(openNow);
  for (const r of installment.resolved) if (open.has(r.question)) store.resolveQuestion(threadId, r.question, runId, r.how);
  const fresh = installment.new_questions;
  // Stored unchanged and suppressed at render time: dropping one here would erase it for good.
  if (fresh.length && JSON.stringify(cleanQuestions(fresh, citedIds)) !== JSON.stringify(fresh))
    console.warn(JSON.stringify({ stage: "threads", warning: "a new question cites an article id inline; the public ledger will suppress it", thread_id: threadId }));
  if (fresh.length) store.addQuestions(threadId, fresh, runId);
  store.setInstallmentContent(threadId, runId, JSON.stringify(verified));
  return verified;
}
