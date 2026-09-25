import type { UsageRow } from "../store/usage.js";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ApplicationFailure } from "@temporalio/common";
import { parse } from "csv-parse/sync";
import { joinTags, type Cluster } from "../cluster/join.js";
import { coerceTag, ExtractItemsSchema, extractItemsJsonSchema, itemsForBatch, TAG_BAG_WEIGHTS, usable, type Tag } from "../cluster/tags.js";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import type { ExtractBatch } from "./index.js";

export const EXTRACT_BATCH = 40;
export const SUMMARY_CAP = 300; // chars of summary shown per article (the title carries most of the signal)
// Reject the stage if more than this fraction of articles fall back to title-only tags: extraction is
// broken, and a title-only partition is near-degenerate. Fail closed rather than ship it.
export const MAX_FALLBACK_FRACTION = 0.25;
export const CLUSTERS_OUTPUT = "clusters.json";
const batchName = (i: number) => `cluster_tags_b${i}.json`;

export interface Article {
  article_id: string;
  title: string;
  summary: string;
  source_id: string;
}

export async function loadArticles(store: ArtifactStore, runId: number): Promise<Article[]> {
  const out: Article[] = [];
  for (const name of (await store.names(runId)).filter((n) => /^articles_\d+\.csv$/.test(n))) {
    const p = await store.find(runId, name);
    if (!p) continue;
    const rows = parse(await store.get(p), { columns: true, skip_empty_lines: true, relax_column_count: true }) as Record<string, string>[];
    for (const r of rows) if (r["article_id"]) out.push({ article_id: r["article_id"], title: r["title"] ?? "", summary: r["summary"] ?? "", source_id: r["source_id"] ?? "" });
  }
  return out;
}

export function planBatches(articles: Article[], size = EXTRACT_BATCH): ExtractBatch[] {
  const ids = articles.map((a) => a.article_id);
  return Array.from({ length: Math.ceil(ids.length / size) }, (_, i) => ({ index: i, ids: ids.slice(i * size, (i + 1) * size) }));
}

// TSV of (article_id, title, summary) for one extraction batch, as the Python stage builds it.
// Summaries are scrubbed of links: the words are the clustering signal, the address is not.
const clean = (s: string) => scrubUrls(s.replaceAll("\n", " ").replaceAll("\t", " "));
// JSON with object keys sorted at every level, as the Python's sort_keys=True writes it.
const stableJson = (v: unknown) => JSON.stringify(v, (_k, val: unknown) => (val && typeof val === "object" && !Array.isArray(val) ? Object.fromEntries(Object.entries(val as Record<string, unknown>).toSorted(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : val));

export function buildExtractPrompt(batch: string[], arts: Map<string, Article>): string {
  const rows = ["article_id\ttitle\tsummary"];
  for (const aid of batch) {
    const a = arts.get(aid);
    if (a) rows.push(`${aid}\t${clean(a.title)}\t${clean(a.summary).slice(0, SUMMARY_CAP)}`);
  }
  return "Extract clustering metadata for these articles:\n\n" + rows.join("\n");
}

export interface ClusterDeps {
  signal?: () => AbortSignal | undefined;
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: UsageRow) => void | Promise<void>;
}

type TagFile = { items: (Tag & { article_id: string })[] };
const validTagFile = (text: string, batch: string[]): boolean => {
  try {
    const items = (JSON.parse(text) as TagFile).items;
    const wanted = new Set(batch);
    return Array.isArray(items) && items.some((it) => wanted.has(it.article_id) && usable(it));
  } catch {
    return false;
  }
};

export function clusterActivities(deps: ClusterDeps) {
  const { store } = deps;

  return {
    planBatches: async (runId: number): Promise<{ batches: ExtractBatch[] }> => {
      const articles = await loadArticles(store, runId);
      if (articles.length === 0) throw ApplicationFailure.nonRetryable(`run ${runId} has no articles_*.csv; prepare has not run`, "MissingInput");
      return { batches: planBatches(articles) };
    },

    // One model call over one batch, structured output decoded against the items schema (shape,
    // never count). Idempotent on its own artifact; zero usable items is a retryable failure, so
    // Temporal's bounded retry replaces the Python stage's blind single re-attempt.
    extractBatch: async (runId: number, batch: ExtractBatch, force = false): Promise<Pointer> => {
      const name = batchName(batch.index);
      const existing = await store.find(runId, name);
      if (existing && !force) {
        if (validTagFile(await store.get(existing), batch.ids)) return existing;
        await store.quarantine(runId, name);
      }
      const arts = new Map((await loadArticles(store, runId)).map((a) => [a.article_id, a]));
      const prompt = buildExtractPrompt(batch.ids, arts);
      assertNoUrls(prompt);
      const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "cluster-extract.md"), "utf8"));
      deps.heartbeat?.();
      const r = await runStage(spec, { userMessage: prompt, inputDir: tmpdir() }, { today: await store.runDate(runId), runId, outputSchema: extractItemsJsonSchema(), ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}) });
      deps.heartbeat?.();
      await deps.onUsage?.({ model: spec.model, thinking: spec.thinking, prompt: spec, effort: r.effort, tokens: r.usage, stage: "cluster-extract", runId, batch: batch.index, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns });
      const parsed = ExtractItemsSchema.safeParse(r.structured);
      if (!parsed.success) throw new Error(`extract batch ${batch.index}: structured output did not match the items schema`);
      const items = itemsForBatch(parsed.data.items, batch.ids)
        .map((it) => ({ article_id: it.article_id, ...coerceTag(it) }))
        .filter((it) => usable(it));
      if (items.length === 0) throw new Error(`extract batch ${batch.index}: 0/${batch.ids.length} articles extracted (${parsed.data.items.length} items parsed)`);
      const text = JSON.stringify({ items } satisfies TagFile);
      return force ? await store.replace(runId, name, text) : await store.put(runId, name, text);
    },

    // The deterministic join over every batch's tags. Coverage is gated on usable tags: a lost
    // batch or an extractor echoing empty schema both yield tagless articles, and too many of those
    // is a degenerate partition the run must not ship. The minority fall back to title-only.
    joinClusters: async (runId: number, tagBatches: (Pointer | null)[], force = false): Promise<Pointer> => {
      const existing = await store.find(runId, CLUSTERS_OUTPUT);
      if (existing && !force) {
        const doc = JSON.parse(await store.get(existing)) as { clusters?: unknown[] };
        if (Array.isArray(doc.clusters) && doc.clusters.length > 0) return existing;
      }
      // The three outputs are one write: regenerating quarantines every sibling a previous attempt
      // left, or put would conflict on the ones that differ.
      if (!force) for (const name of [CLUSTERS_OUTPUT, "cluster_tags.json", "cluster_health.json"]) if (await store.find(runId, name)) await store.quarantine(runId, name);
      const articles = await loadArticles(store, runId);
      const ids = articles.map((a) => a.article_id);
      const tags: Record<string, Tag> = {};
      for (const p of tagBatches) if (p) for (const it of (JSON.parse(await store.get(p)) as TagFile).items) tags[it.article_id] = coerceTag(it);
      const missing = ids.filter((a) => !usable(tags[a]));
      if (missing.length > ids.length * MAX_FALLBACK_FRACTION)
        throw ApplicationFailure.nonRetryable(`extract-join: ${missing.length}/${ids.length} articles fell back to title-only (> ${MAX_FALLBACK_FRACTION * 100}%); refusing a degenerate partition`, "DegeneratePartition");
      const byId = new Map(articles.map((a) => [a.article_id, a]));
      for (const a of missing) tags[a] = { entities: [], keywords: [], primary_event: (byId.get(a)?.title ?? "").slice(0, 60) };
      const clusters: Cluster[] = joinTags(ids, tags);
      if (clusters.length === 0) throw new Error("extract-join: no clusters");
      const write = (name: string, text: string) => (force ? store.replace(runId, name, text) : store.put(runId, name, text));
      await write("cluster_tags.json", stableJson({ tag_bag_weights: TAG_BAG_WEIGHTS, tags }));
      await write("cluster_health.json", JSON.stringify({ articles: ids.length, title_only_fallback: missing.length, batches_lost: tagBatches.filter((p) => !p).length, tags_archived: true }));
      return write(CLUSTERS_OUTPUT, JSON.stringify({ clusters }, null, 2));
    },
  };
}
