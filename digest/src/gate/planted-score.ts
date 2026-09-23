import type { CoherenceReport } from "../contracts/coherence.js";
import { itemIds, normHeadline, resultMatches } from "../contracts/match.js";

type Field = "headline" | "summary" | "why_it_matters";
export interface PlantedKey { hard_positives: { idx: number; field: Field }[]; clean_fields: { idx: number; field: Field }[] }
interface DraftStory { headline: string; sources: { article_id: string }[] }

// Which fields the checker failed, per story in draft order (must_know then should_know): the index
// the planted key uses.
export function flaggedFields(report: CoherenceReport, draft: { must_know: DraftStory[]; should_know?: DraftStory[] }): Set<string>[] {
  return [...draft.must_know, ...(draft.should_know ?? [])].map((s) => {
    const hits = report.results.filter((r) => !r.pass && resultMatches(r, itemIds(s.sources), normHeadline(s.headline)));
    return new Set(hits.flatMap((h) => h.failed_fields ?? []));
  });
}

export function scorePlanted(report: CoherenceReport, draft: { must_know: DraftStory[]; should_know?: DraftStory[] }, key: PlantedKey) {
  const flagged = flaggedFields(report, draft);
  const hit = (c: { idx: number; field: Field }) => flagged[c.idx]?.has(c.field) ?? false;
  const missed = key.hard_positives.filter((c) => !hit(c));
  const falseDrops = key.clean_fields.filter(hit);
  return {
    recall: key.hard_positives.length - missed.length,
    planted: key.hard_positives.length,
    falseDrops: falseDrops.length,
    clean: key.clean_fields.length,
    missed: missed.map((c) => `${c.idx}:${c.field}`),
    dropped: falseDrops.map((c) => `${c.idx}:${c.field}`),
  };
}
