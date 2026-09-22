import { ApplicationFailure } from "@temporalio/common";
import { CoherenceReportSchema } from "../contracts/coherence.js";
import { leaksInternalId, stripArticleIds } from "../contracts/leaks.js";
import { itemIds, normHeadline, resultMatches } from "../contracts/match.js";
import { NOT_COVERED_BLURB_MAX_LEN, PREHEADER_MAX_CHARS, SelectionsSchema, type Selections } from "../contracts/selections.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { draftFrom } from "./coherence.js";
import { truncateOnWordBoundary } from "./preheader.js";
import type { ResolutionDoc } from "./repair.js";
import { SelectedSchema } from "./select.js";

export const SELECTIONS_OUTPUT = "selections.json";

// A repair is taken whole or not at all: every patched value a repairable, non-empty, leak-free string.
const validPatch = (p: Record<string, unknown>) => Object.keys(p).length > 0 && Object.entries(p).every(([f, v]) => ["headline", "summary", "why_it_matters"].includes(f) && typeof v === "string" && v.trim() !== "" && !leaksInternalId(v));

// The cluster label holding the most of a story's distinct cited ids, ties to the earliest cited
// (utils.cluster_for_articles): the join key threads and render share.
export function clusterFor(ids: string[], owner: Map<string, string>): string | undefined {
  const counts = new Map<string, number>();
  for (const a of new Set(ids)) {
    const s = owner.get(a);
    if (s) counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  let best: string | undefined;
  for (const [s, n] of counts) if (best === undefined || n > (counts.get(best) ?? 0)) best = s;
  return best;
}

export interface AssembleReport { shipped: number; dropped: string[]; repaired: number; blanked: number }

// merge.assemble_selections, ported. For each story, in order: passed → keep; repaired with a
// patch covering exactly the flagged fields and a passed recheck → keep, patched; only
// why_it_matters failed → keep, blanked (a brief has none to blank); anything else → drop. No
// must_know left → abort rather than send an empty digest. Then the reader-facing guards and the
// contract.
export function assemble(store: ArtifactStore, runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer | null): { selections: Selections; report: AssembleReport } {
  const draft = draftFrom(store, drafts);
  const results = CoherenceReportSchema.parse(JSON.parse(store.get(report))).results;
  const resolution = JSON.parse(store.get(repair)) as ResolutionDoc;
  const patches = new Map(resolution.results.filter((r) => r.status === "repaired" && r.recheck_pass && validPatch(r.patched_fields)).map((r) => [r.article_ids.toSorted().join(","), r.patched_fields]));
  const owner = new Map<string, string>();
  const clusters = store.find(runId, "clusters.json");
  if (clusters) for (const c of (JSON.parse(store.get(clusters)) as { clusters: { story: string; article_ids: string[] }[] }).clusters) for (const a of c.article_ids) owner.set(a, c.story);
  const out: AssembleReport = { shipped: 0, dropped: [], repaired: 0, blanked: 0 };
  const sel: Record<"must_know" | "should_know", Selections["must_know"]> = { must_know: [], should_know: [] };
  for (const tier of ["must_know", "should_know"] as const)
    for (const story of draft[tier]) {
      const item = { ...story, why_it_matters: story.why_it_matters ?? "" };
      if (item.reporting_varies) {
        const rv = item.reporting_varies.map((e) => ({ source: stripArticleIds(e.source), angle: stripArticleIds(e.angle), bias: stripArticleIds(e.bias) })).filter((e) => e.source && e.angle);
        if (rv.length) item.reporting_varies = rv;
        else delete item.reporting_varies;
      }
      const ids = itemIds(story.sources);
      const hits = results.filter((r) => !r.pass && resultMatches(r, ids, normHeadline(story.headline)));
      if (hits.length) {
        const flagged = new Set(hits.flatMap((h) => h.failed_fields ?? []));
        const repairable = hits.every((h) => (h.failed_fields ?? []).length > 0);
        const patch = patches.get([...ids].toSorted().join(","));
        if (patch && repairable && Object.keys(patch).length === flagged.size && Object.keys(patch).every((f) => flagged.has(f as never))) {
          Object.assign(item, patch);
          out.repaired++;
        } else if (hits.every((h) => h.failed_fields?.length === 1 && h.failed_fields[0] === "why_it_matters")) {
          if (tier === "must_know") {
            item.why_it_matters = "";
            out.blanked++;
          }
        } else {
          out.dropped.push(story.headline);
          continue;
        }
      }
      const cid = clusterFor(story.sources.map((s) => s.article_id), owner);
      const kept = { ...item, ...(cid ? { cluster_id: cid } : {}) };
      if (tier === "should_know") delete (kept as { why_it_matters?: string }).why_it_matters;
      sel[tier].push(kept);
    }
  if (!sel.must_know.length) throw ApplicationFailure.nonRetryable(`run ${runId}: no must_know story survived (dropped ${out.dropped.length}); refusing an empty broadcast`, "EmptyDigest");
  let pre = preheader ? store.get(preheader).trim() : "";
  if (!pre) pre = [...sel.must_know, ...sel.should_know].map((s) => s.headline.trim()).find(Boolean) ?? "";
  const selected = store.find(runId, "selected.json");
  let blurb: string | undefined;
  if (selected) {
    const b = SelectedSchema.safeParse(JSON.parse(store.get(selected))).data?.not_covered_blurb?.trim();
    if (b && !leaksInternalId(b)) blurb = truncateOnWordBoundary(b, NOT_COVERED_BLURB_MAX_LEN);
  }
  const selections = { must_know: sel.must_know, should_know: sel.should_know, preheader: truncateOnWordBoundary(pre, PREHEADER_MAX_CHARS), ...(blurb ? { not_covered_blurb: blurb } : {}) };
  const parsed = SelectionsSchema.safeParse(selections);
  if (!parsed.success) throw ApplicationFailure.nonRetryable(`run ${runId}: assembled selections fail the contract: ${parsed.error.issues.slice(0, 3).map((i) => `${i.path.join(".")} ${i.message}`).join("; ")}`, "ContractViolation");
  out.shipped = sel.must_know.length + sel.should_know.length;
  return { selections: parsed.data, report: out };
}

export function assembleActivity(deps: { store: ArtifactStore }) {
  return async (runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer | null, force = false): Promise<Pointer> => {
    const { selections, report: r } = assemble(deps.store, runId, drafts, report, repair, preheader);
    console.log(JSON.stringify({ stage: "assemble", runId, ...r }));
    const text = JSON.stringify(selections, null, 2);
    const existing = deps.store.find(runId, SELECTIONS_OUTPUT);
    if (existing && !force && deps.store.get(existing) !== text) deps.store.quarantine(runId, SELECTIONS_OUTPUT);
    return force ? deps.store.replace(runId, SELECTIONS_OUTPUT, text) : deps.store.put(runId, SELECTIONS_OUTPUT, text);
  };
}
