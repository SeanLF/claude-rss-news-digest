import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { buildExtractPrompt, clusterActivities, planBatches, type Article } from "./cluster.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const CSV = 'article_id,title,summary,source_id\nA1,Sweden votes,"Centre-left wins, SVT says",reuters\nA2,Swedish election result,Opposition ahead in count,bbc\nA3,Kane on Ballon d\'Or list,Striker shortlisted,guardian\nA4,Sweden count continues,Late ballots,dn\nA5,Sweden far right stalls,Analysis,svt\n';

function fakeQuery(structured: unknown, seen: { prompts: string[]; options?: Options }): SdkQuery {
  return (({ prompt, options }: { prompt: string; options?: Options }) => {
    seen.prompts.push(prompt);
    if (options) seen.options = options;
    return (function* () {
      yield { type: "result", subtype: "success", result: JSON.stringify(structured), structured_output: structured, total_cost_usd: 0.02, usage: {}, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}
const sweden = (id: string) => ({ article_id: id, entities: ["Sweden"], keywords: ["election"], primary_event: "Sweden election" });

async function setup(structured: unknown) {
  const store = new ArtifactStore(await freshDb([300]));
  await store.put(300, "articles_1.csv", CSV);
  const seen: { prompts: string[]; options?: Options } = { prompts: [] };
  const acts = clusterActivities({ store, agentsDir: AGENTS, query: fakeQuery(structured, seen) });
  return { store, seen, acts };
}

describe("cluster activities", () => {
  it("plans batches of 40 in article order and builds the TSV prompt", async () => {
    const arts: Article[] = Array.from({ length: 85 }, (_, i) => ({ article_id: `A${i + 1}`, title: `t${i}`, summary: "s", source_id: "x" }));
    const batches = planBatches(arts);
    expect(batches.map((b) => b.ids.length)).toEqual([40, 40, 5]);
    expect(batches[2]).toEqual({ index: 2, ids: ["A81", "A82", "A83", "A84", "A85"] });
    const prompt = buildExtractPrompt(["A1"], new Map([["A1", { article_id: "A1", title: "a\tb", summary: "x".repeat(400), source_id: "s" }]]));
    expect(prompt).toContain("article_id\ttitle\tsummary\nA1\ta b\t" + "x".repeat(300));
  });
  it("extracts a batch through the runner with the items schema, scopes items to the batch, and persists usable tags", async () => {
    const { store, seen, acts } = await setup({ items: [sweden("A1"), sweden("A2"), sweden("A99"), { article_id: "A3", entities: [], keywords: [], primary_event: "" }] });
    const { batches } = await acts.planBatches(300);
    expect(batches).toEqual([{ index: 0, ids: ["A1", "A2", "A3", "A4", "A5"] }]);
    const p = await acts.extractBatch(300, batches[0]!);
    expect(seen.options?.outputFormat).toMatchObject({ type: "json_schema" });
    expect(seen.options?.tools).toEqual([]);
    expect(seen.prompts[0]).toContain("A2\tSwedish election result\tOpposition ahead in count");
    const items = (JSON.parse(await store.get(p)) as { items: { article_id: string }[] }).items.map((i) => i.article_id);
    expect(items).toEqual(["A1", "A2"]); // A99 is not in the batch; A3 has no usable tags
    expect(await acts.extractBatch(300, batches[0]!)).toEqual(p); // idempotent
    expect(seen.prompts.length).toBe(1);
  });
  it("a batch with zero usable items is a retryable failure and stores nothing", async () => {
    const { store, acts } = await setup({ items: [{ article_id: "A1", entities: null, keywords: [], primary_event: null }] });
    await expect(acts.extractBatch(300, { index: 0, ids: ["A1", "A2", "A3"] })).rejects.toThrow(/0\/3 articles extracted/);
    expect(await store.find(300, "cluster_tags_b0.json")).toBeUndefined();
  });
  it("joins the batches into clusters, title-falls back the tagless minority, and records health", async () => {
    const { store, acts } = await setup({ items: [sweden("A1"), sweden("A2"), sweden("A4"), sweden("A5")] });
    const p = await acts.extractBatch(300, { index: 0, ids: ["A1", "A2", "A3", "A4", "A5"] });
    const c = await acts.joinClusters(300, [p]);
    const { clusters } = JSON.parse(await store.get(c)) as { clusters: { story: string; article_ids: string[] }[] };
    expect(clusters.map((x) => x.article_ids)).toEqual([["A1", "A2", "A4", "A5"], ["A3"]]);
    expect(clusters[1]!.story).toBe("Kane on Ballon d'Or list");
    expect(JSON.parse(await store.content(300, "cluster_health.json"))).toEqual({ articles: 5, title_only_fallback: 1, batches_lost: 0, tags_archived: true });
    expect(JSON.parse(await store.content(300, "cluster_tags.json"))).toMatchObject({ tag_bag_weights: { entities: 3, keywords: 1, primary_event: 2 } });
    expect(await acts.joinClusters(300, [p])).toEqual(c); // idempotent
  });
  it("scrubs a link inside a summary before it reaches the extract prompt", async () => {
    const prompt = buildExtractPrompt(["A1"], new Map([["A1", { article_id: "A1", title: "Jemalloc 5.4.0", summary: "Article URL: https://github.com/jemalloc/releases Comments: https://news.ycombinator.com/item?id=1", source_id: "hn" }]]));
    expect(prompt).toContain("A1\tJemalloc 5.4.0\tArticle URL: [link] Comments: [link]");
    expect(prompt).not.toContain("http");
  });
  it("regenerating after an invalid clusters.json quarantines the sibling outputs too", async () => {
    const { store, acts } = await setup({ items: [sweden("A1"), sweden("A2"), sweden("A4"), sweden("A5")] });
    const p = await acts.extractBatch(300, { index: 0, ids: ["A1", "A2", "A3", "A4", "A5"] });
    await store.put(300, "clusters.json", '{"clusters": []}');
    await store.put(300, "cluster_health.json", '{"stale": true}');
    const c = await acts.joinClusters(300, [p]);
    expect((JSON.parse(await store.get(c)) as { clusters: unknown[] }).clusters.length).toBe(2);
    expect(await store.states(300, "clusters.json")).toContain("quarantined");
    expect(await store.states(300, "cluster_health.json")).toContain("quarantined");
  });
  it("refuses a degenerate partition when too many articles are tagless", async () => {
    const { acts } = await setup({ items: [] });
    await expect(acts.joinClusters(300, [null])).rejects.toMatchObject({ nonRetryable: true, type: "DegeneratePartition" });
  });
});
