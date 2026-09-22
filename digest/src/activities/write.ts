import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ApplicationFailure } from "@temporalio/common";
import { parse } from "csv-parse/sync";
import { stringify } from "csv-stringify/sync";
import { z } from "zod";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import type { StoryPlan } from "./index.js";
import { SelectedSchema } from "./select.js";

export const WRITE_BRANCH_BUDGET_USD = 1.0;
const SHARED = ["recap.txt", "weekly_recap.txt", "recent_digest_headlines.txt"];
export const draftName = (index: number) => `draft_s${String(index).padStart(2, "0")}.json`;

// The cluster holding the most of the story's distinct citations; ties keep the earliest cited.
// SELECT's cluster_index is a position a model counts into hundreds of clusters and drifts (14% of
// archived stories), so the citations decide.
export function resolveClusterIndex(clusters: { article_ids: string[] }[], storyIds: string[]): number | undefined {
  const owner = new Map<string, number>();
  clusters.forEach((c, i) => c.article_ids.forEach((a) => owner.has(a) || owner.set(a, i)));
  const counts = new Map<number, number>();
  for (const a of new Set(storyIds)) {
    const h = owner.get(a);
    if (h !== undefined) counts.set(h, (counts.get(h) ?? 0) + 1);
  }
  let best: number | undefined;
  for (const [h, n] of counts) if (best === undefined || n > (counts.get(best) ?? 0)) best = h;
  return best;
}

// One plan per selected story in SELECT's order: its tier, its citations, and the evidence it may
// see (its cluster unioned with its citations, restricted to ids the run has). A story with no
// evidence is dropped rather than written from nothing.
export function planStories(selectedJson: string, clustersJson: string, knownIds: ReadonlySet<string>): { plans: StoryPlan[]; dropped: { index: number; tier: string; reason: string }[] } {
  const selected = SelectedSchema.parse(JSON.parse(selectedJson));
  const clusters = (JSON.parse(clustersJson) as { clusters: { article_ids: string[] }[] }).clusters;
  const plans: StoryPlan[] = [];
  const dropped: { index: number; tier: string; reason: string }[] = [];
  let index = 0;
  for (const tier of ["must_know", "should_know"] as const)
    for (const pick of selected[tier]) {
      // The citations decide; SELECT's stated index is only the fallback when no cited id has a cluster.
      const byCitation = resolveClusterIndex(clusters, pick.article_ids);
      const resolved = byCitation ?? (pick.cluster_index >= 0 && pick.cluster_index < clusters.length ? pick.cluster_index : undefined);
      const cluster = resolved !== undefined ? clusters[resolved]!.article_ids : [];
      const contextIds = [...new Set([...cluster, ...pick.article_ids])].filter((a) => knownIds.has(a));
      if (contextIds.length === 0) dropped.push({ index, tier, reason: "no article in this run's CSVs" });
      else plans.push({ index, tier, storyIds: pick.article_ids, contextIds, ...(resolved !== undefined ? { clusterIndex: resolved } : {}) });
      index++;
    }
  return { plans, dropped };
}

const DraftStory = z.object({
  headline: z.string(),
  summary: z.string(),
  why_it_matters: z.string().optional(),
  sources: z.array(z.object({ article_id: z.string() })),
  reporting_varies: z.array(z.object({ source: z.string(), angle: z.string(), bias: z.string() })).optional(),
});
// The prompt's own output shape, kept so the prompt text carries over; shape only, never count.
export const BranchDraftSchema = z.object({ must_know: z.array(DraftStory).optional(), should_know: z.array(DraftStory).optional() });
export type DraftStory = z.infer<typeof DraftStory>;

// Exactly one well-formed story, the fields its tier requires, and only citations to the branch's
// own evidence; anything else fails retryably and the next attempt is a fresh sample.
export function checkBranch(draft: z.infer<typeof BranchDraftSchema>, plan: StoryPlan): { story?: DraftStory; problems: string[] } {
  const stories = [...(draft.must_know ?? []), ...(draft.should_know ?? [])];
  if (stories.length !== 1) return { problems: [`expected exactly 1 story, found ${stories.length}`] };
  const story = { ...stories[0]! };
  const problems: string[] = [];
  for (const f of ["headline", "summary"] as const) if (!story[f].trim()) problems.push(`missing ${f}`);
  if (plan.tier === "must_know" && !story.why_it_matters?.trim()) problems.push("missing why_it_matters");
  if (story.sources.length === 0) problems.push("no sources");
  const allowed = new Set(plan.contextIds);
  const stray = story.sources.map((s) => s.article_id).filter((a) => !allowed.has(a));
  if (stray.length) problems.push(`cites ids outside its evidence: ${stray.slice(0, 5).join(",")}`);
  if (plan.tier === "should_know") delete story.why_it_matters; // briefs render no why_it_matters
  return { story, problems };
}

export interface WriteDeps {
  signal?: () => AbortSignal | undefined;
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: { stage: string; runId: number; story: number; costUsd: number; durationMs: number; numTurns: number; toolCalls: number }) => void;
}

function runArticles(store: ArtifactStore, runId: number): { ids: Set<string>; header: string[]; rows: Record<string, string>[] } {
  const rows: Record<string, string>[] = [];
  let header: string[] = [];
  for (const name of store.names(runId).filter((n) => /^articles_\d+\.csv$/.test(n))) {
    const text = store.get(store.find(runId, name)!);
    const recs = parse<Record<string, string>>(text, { columns: true, skip_empty_lines: true, relax_column_count: true });
    if (recs[0] && header.length === 0) header = Object.keys(recs[0]);
    rows.push(...recs);
  }
  return { ids: new Set(rows.map((r) => r["article_id"] ?? "").filter(Boolean)), header, rows };
}

export function writeActivities(deps: WriteDeps) {
  const { store } = deps;
  return {
    planStories: async (runId: number, selected: Pointer, clusters: Pointer): Promise<{ plans: StoryPlan[] }> => {
      const { ids } = runArticles(store, runId);
      const { plans, dropped } = planStories(store.get(selected), store.get(clusters), ids);
      if (dropped.length) console.error(JSON.stringify({ stage: "write-plan", runId, dropped }));
      if (plans.length === 0) throw ApplicationFailure.nonRetryable(`run ${runId}: no selected story has evidence to write from`, "NothingToWrite");
      return { plans };
    },

    writeStory: async (runId: number, plan: StoryPlan, selected: Pointer, note?: string, force = false): Promise<Pointer> => {
      const name = draftName(plan.index);
      const existing = store.find(runId, name);
      if (existing && !force) {
        const prior = JSON.parse(store.get(existing)) as { plan?: StoryPlan };
        if (JSON.stringify(prior.plan) === JSON.stringify(plan)) return existing;
        store.quarantine(runId, name); // written for a different story or evidence
      }
      const sel = SelectedSchema.parse(JSON.parse(store.get(selected)));
      const pick = { article_ids: plan.storyIds, ...(plan.clusterIndex !== undefined ? { cluster_index: plan.clusterIndex } : {}) };
      const one = { must_know: plan.tier === "must_know" ? [pick] : [], should_know: plan.tier === "should_know" ? [pick] : [], ...(sel.not_covered_blurb ? { not_covered_blurb: sel.not_covered_blurb } : {}) };
      const { header, rows } = runArticles(store, runId);
      const keep = new Set(plan.contextIds);
      const dir = mkdtempSync(join(tmpdir(), `write-${runId}-s${plan.index}-`));
      try {
        const put = (file: string, text: string) => {
          const clean = scrubUrls(text);
          assertNoUrls(clean);
          writeFileSync(join(dir, file), clean);
        };
        put("selected.json", JSON.stringify(one, null, 2));
        put("articles_1.csv", stringify(rows.filter((r) => keep.has(r["article_id"] ?? "")), { header: true, columns: header }));
        const ft = store.find(runId, "article_fulltext.json");
        if (ft) {
          const all = JSON.parse(store.get(ft)) as Record<string, unknown>;
          put("article_fulltext.json", JSON.stringify(Object.fromEntries(Object.entries(all).filter(([k]) => keep.has(k))), null, 2));
        }
        for (const f of SHARED) {
          const p = store.find(runId, f);
          if (p) put(f, store.get(p));
        }
        const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "write.md"), "utf8"));
        const message = `The input directory is ${dir}. Begin.${note ? `\n\nOperator note for this attempt: ${note}` : ""}`;
        deps.heartbeat?.();
        const r = await runStage(spec, { userMessage: message, inputDir: dir }, {
          today: store.runDate(runId),
          outputSchema: z.toJSONSchema(BranchDraftSchema, { target: "draft-07" }),
          maxBudgetUsd: WRITE_BRANCH_BUDGET_USD,
          ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}),
        });
        deps.heartbeat?.();
        deps.onUsage?.({ stage: "write", runId, story: plan.index, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, toolCalls: r.toolCalls.length });
        const parsed = BranchDraftSchema.safeParse(r.structured);
        if (!parsed.success) throw new Error(`write s${plan.index}: output does not match the schema`);
        const { story, problems } = checkBranch(parsed.data, plan);
        if (problems.length || !story) throw new Error(`write s${plan.index}: ${problems.join("; ")}`);
        const text = JSON.stringify({ plan, story }, null, 2);
        return force ? store.replace(runId, name, text) : store.put(runId, name, text);
      } finally {
        rmSync(dir, { recursive: true, force: true }); // the mkdtemp directory this call created
      }
    },
  };
}
