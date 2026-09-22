import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { joinTags, mergeSameStory } from "./join.js";
import type { Tag } from "./tags.js";

const fixture = (name: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const partition = (clusters: { article_ids: string[] }[]) => new Set(clusters.map((c) => c.article_ids.toSorted().join(",")));

describe("joinTags", () => {
  it("reproduces run 300's archived clusters.json from its archived cluster_tags.json (sklearn parity)", () => {
    const { tags } = fixture("run300-cluster_tags.json") as { tags: Record<string, Tag> };
    const { clusters: archived } = fixture("run300-clusters.json") as { clusters: { story: string; article_ids: string[] }[] };
    const ids = archived.flatMap((c) => c.article_ids).toSorted((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
    const ours = joinTags(ids, tags, 0.8);
    expect(ours.flatMap((c) => c.article_ids).length).toBe(ids.length);
    const same = [...partition(ours)].filter((p) => partition(archived).has(p)).length;
    expect({ ours: ours.length, archived: archived.length, identical: same }).toEqual({ ours: archived.length, archived: archived.length, identical: archived.length });
  });
  it("keeps tagless articles as singletons and labels by the modal event", () => {
    const tags: Record<string, Tag> = {
      A1: { entities: ["Sweden"], keywords: ["election"], primary_event: "Sweden election" },
      A2: { entities: ["Sweden"], keywords: ["vote"], primary_event: "Sweden election" },
      A3: { entities: [], keywords: [], primary_event: "" },
    };
    const out = joinTags(["A1", "A2", "A3"], tags, 0.8);
    expect(out.map((c) => c.article_ids)).toEqual([["A1", "A2"], ["A3"]]);
    expect(out[0]!.story).toBe("Sweden election");
    expect(out[1]!.story).toBe("cluster 2");
  });
});

describe("mergeSameStory", () => {
  it("folds a fragment into the largest same-label cluster and leaves a substantial sibling separate", () => {
    const out = mergeSameStory([
      { story: "Iran", article_ids: ["A1", "A2", "A3"] },
      { story: "Iran", article_ids: ["A9"] },
      { story: "Iran", article_ids: ["A4", "A5", "A6"] },
      { story: "Other", article_ids: ["A7"] },
    ]);
    expect(out).toEqual([
      { story: "Iran", article_ids: ["A1", "A2", "A3", "A9"] },
      { story: "Iran", article_ids: ["A4", "A5", "A6"] },
      { story: "Other", article_ids: ["A7"] },
    ]);
  });
});
