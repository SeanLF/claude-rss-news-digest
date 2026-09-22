import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import type { StoryPlan } from "./index.js";
import { checkBranch, draftName, planStories, resolveClusterIndex, writeActivities } from "./write.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const clusters = { clusters: [{ story: "a", article_ids: ["A1", "A2"] }, { story: "b", article_ids: ["A3", "A4", "A5"] }] };

describe("planning", () => {
  it("resolves the cluster by the most distinct citations, earliest on a tie", () => {
    expect(resolveClusterIndex(clusters.clusters, ["A3", "A4", "A1"])).toBe(1);
    expect(resolveClusterIndex(clusters.clusters, ["A1", "A3"])).toBe(0);
    expect(resolveClusterIndex(clusters.clusters, ["A99"])).toBeUndefined();
  });
  it("plans stories in SELECT's order with their cluster as evidence, and drops a story with none", () => {
    const selected = { must_know: [{ cluster_index: 0, article_ids: ["A4"] }], should_know: [{ cluster_index: 7, article_ids: ["A99"] }, { cluster_index: 0, article_ids: ["A2"] }] };
    const { plans, dropped } = planStories(JSON.stringify(selected), JSON.stringify(clusters), new Set(["A1", "A2", "A3", "A4", "A5"]));
    expect(plans).toEqual([
      { index: 0, tier: "must_know", storyIds: ["A4"], contextIds: ["A3", "A4", "A5"], clusterIndex: 1 },
      { index: 2, tier: "should_know", storyIds: ["A2"], contextIds: ["A1", "A2"], clusterIndex: 0 },
    ]);
    expect(dropped).toEqual([{ index: 1, tier: "should_know", reason: "no article in this run's CSVs" }]);
  });
  it("falls back to SELECT's stated cluster when no citation has one", () => {
    const selected = { must_know: [{ cluster_index: 1, article_ids: ["A7"] }], should_know: [] };
    const { plans } = planStories(JSON.stringify(selected), JSON.stringify(clusters), new Set(["A3", "A4", "A5", "A7"]));
    expect(plans).toEqual([{ index: 0, tier: "must_know", storyIds: ["A7"], contextIds: ["A3", "A4", "A5", "A7"], clusterIndex: 1 }]);
  });
});

const plan: StoryPlan = { index: 0, tier: "must_know", storyIds: ["A1"], contextIds: ["A1", "A2"], clusterIndex: 0 };
const story = { headline: "H", summary: "S", why_it_matters: "W", sources: [{ article_id: "A1" }] };

describe("checkBranch", () => {
  it("accepts one story with its tier's fields and in-evidence citations", () => {
    expect(checkBranch({ must_know: [story] }, plan)).toEqual({ story, problems: [] });
  });
  it("a brief loses why_it_matters; a must_know without one fails", () => {
    expect(checkBranch({ should_know: [story] }, { ...plan, tier: "should_know" }).story).not.toHaveProperty("why_it_matters");
    expect(checkBranch({ must_know: [{ ...story, why_it_matters: " " }] }, plan).problems).toEqual(["missing why_it_matters"]);
  });
  it("fails on two stories, no sources, or a citation outside the evidence", () => {
    expect(checkBranch({ must_know: [story, story] }, plan).problems).toEqual(["expected exactly 1 story, found 2"]);
    expect(checkBranch({ must_know: [{ ...story, sources: [] }] }, plan).problems).toEqual(["no sources"]);
    expect(checkBranch({ must_know: [{ ...story, sources: [{ article_id: "A9" }] }] }, plan).problems).toEqual(["cites ids outside its evidence: A9"]);
  });
});

function setup(structured: unknown) {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, "articles_1.csv", 'article_id,source_id,title,published,summary\nA1,hn,T1,2026-09-18,"Article URL: https://x.com/a"\nA2,bbc,T2,2026-09-18,S2\nA3,bbc,T3,2026-09-18,S3\n');
  store.put(300, "clusters.json", JSON.stringify(clusters));
  store.put(300, "article_fulltext.json", JSON.stringify({ A1: "full one", A3: "full three" }));
  store.put(300, "recap.txt", "recap");
  const sel = store.put(300, "selected.json", JSON.stringify({ must_know: [{ cluster_index: 0, article_ids: ["A1"] }], should_know: [], not_covered_blurb: "Held back X." }));
  const seen: { n: number; options?: Options; files?: string[]; csv?: string; ft?: string; selected?: string } = { n: 0 };
  const q = (({ options }: { prompt: string; options?: Options }) => {
    seen.n++;
    if (options) seen.options = options;
    const d = options?.cwd ?? "";
    seen.files = readdirSync(d).toSorted();
    seen.csv = readFileSync(join(d, "articles_1.csv"), "utf8");
    seen.ft = readFileSync(join(d, "article_fulltext.json"), "utf8");
    seen.selected = readFileSync(join(d, "selected.json"), "utf8");
    return (function* () {
      yield { type: "result", subtype: "success", result: "", structured_output: structured, total_cost_usd: 0.3, usage: {}, duration_ms: 5, is_error: false, num_turns: 6, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
  return { store, sel, seen, acts: writeActivities({ store, agentsDir: AGENTS, query: q }) };
}
describe("writeStory activity", () => {
  it("gives the branch only its evidence, links scrubbed, and stores {plan, story} under the story's name", async () => {
    const { store, sel, seen, acts } = setup({ must_know: [story] });
    const p = await acts.writeStory(300, plan, sel);
    expect(p.name).toBe(draftName(0));
    expect(JSON.parse(store.get(p))).toEqual({ plan, story });
    expect(seen.files).toEqual(["article_fulltext.json", "articles_1.csv", "recap.txt", "selected.json"]);
    expect(seen.csv).toContain("A1,hn,T1");
    expect(seen.csv).toContain("A2,bbc,T2");
    expect(seen.csv).not.toContain("A3");
    expect(seen.csv).toContain("[link]");
    expect(JSON.parse(seen.ft ?? "{}")).toEqual({ A1: "full one" });
    expect(JSON.parse(seen.selected ?? "{}")).toEqual({ must_know: [{ article_ids: ["A1"], cluster_index: 0 }], should_know: [], not_covered_blurb: "Held back X." });
    expect(seen.options?.maxBudgetUsd).toBe(1);
    expect(seen.options?.tools).toEqual(["Read", "Grep"]);
    expect(await acts.writeStory(300, plan, sel)).toEqual(p); // idempotent for the same plan
    expect(seen.n).toBe(1);
  });
  it("a draft written for a different plan is quarantined and rewritten", async () => {
    const { store, sel, seen, acts } = setup({ must_know: [story] });
    store.put(300, draftName(0), JSON.stringify({ plan: { ...plan, contextIds: ["A1"] }, story }));
    await acts.writeStory(300, plan, sel);
    expect(seen.n).toBe(1);
    expect(store.find(300, `${draftName(0)}.corrupt.1`)).toBeDefined();
  });
  it("a content problem fails retryably and stores nothing", async () => {
    const { store, sel, acts } = setup({ must_know: [{ ...story, sources: [{ article_id: "A3" }] }] });
    await expect(acts.writeStory(300, plan, sel)).rejects.toThrow(/outside its evidence: A3/);
    expect(store.find(300, draftName(0))).toBeUndefined();
  });
});
