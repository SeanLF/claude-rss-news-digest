import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { checkSelected, selectActivity, SELECT_OUTPUT } from "./select.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const p0 = { runId: 300, name: "x", sha256: "0".repeat(64) };

function fakeQuery(structured: unknown, seen: { n: number; options?: Options; files?: string[]; articles?: string }): SdkQuery {
  return (({ options }: { prompt: string; options?: Options }) => {
    seen.n++;
    if (options) seen.options = options;
    const cwd = options?.cwd ?? "";
    seen.files = readdirSync(cwd).toSorted();
    seen.articles = readFileSync(join(cwd, "articles_1.csv"), "utf8");
    return (function* () {
      yield { type: "result", subtype: "success", result: "", structured_output: structured, total_cost_usd: 0.7, usage: {}, duration_ms: 5, is_error: false, num_turns: 9, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}

function setup(structured: unknown) {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, "articles_1.csv", 'article_id,source_id,title,published,summary\nA1,hn,Jemalloc,2026-09-18,Article URL: https://github.com/x\n"A2",bbc,Vote,2026-09-18,"Count, early"\n');
  store.put(300, "clusters.json", '{"clusters":[{"story":"s","article_ids":["A1","A2"]}]}');
  store.put(300, "recap.txt", "recap");
  store.put(300, "sources.csv", "id,name\nhn,HN\n");
  store.put(300, "weekly_recap.txt", "weekly");
  const seen: { n: number; options?: Options; files?: string[]; articles?: string } = { n: 0 };
  return { store, seen, select: selectActivity({ store, agentsDir: AGENTS, query: fakeQuery(structured, seen) }) };
}
const good = { must_know: [{ cluster_index: 0, article_ids: ["A1", "A2"] }], should_know: [], not_covered_blurb: "Nothing held back." };

describe("select activity", () => {
  it("materialises the inputs with links scrubbed, runs with Read and Grep only, and stores the checked selection", async () => {
    const { store, seen, select } = setup(good);
    const p = await select(300, p0, p0);
    expect(JSON.parse(store.get(p))).toEqual(good);
    expect(seen.files).toEqual(["articles_1.csv", "clusters.json", "recap.txt", "sources.csv", "weekly_recap.txt"]);
    expect(seen.articles).toContain("Article URL: [link]");
    expect(seen.options?.tools).toEqual(["Read", "Grep"]);
    expect(seen.options?.outputFormat).toMatchObject({ type: "json_schema" });
    expect(existsSync(seen.options?.cwd ?? "")).toBe(false); // scratch dir discarded
    expect(await select(300, p0, p0)).toEqual(p); // idempotent
    expect(seen.n).toBe(1);
  });
  it("persists an operator note as an input artifact of the run", async () => {
    const { store, select } = setup(good);
    await select(300, p0, p0, "prefer the Sudan story");
    const name = store.names(300).find((n) => n.startsWith("operator_note.select."));
    expect(name && store.get(store.find(300, name)!)).toBe("prefer the Sudan story");
  });
  it("a citation to an id the run does not have is a retryable failure that stores nothing", async () => {
    const { store, select } = setup({ must_know: [{ cluster_index: 0, article_ids: ["A1", "A77"] }], should_know: [] });
    await expect(select(300, p0, p0)).rejects.toThrow(/unknown ids A77/);
    expect(store.find(300, SELECT_OUTPUT)).toBeUndefined();
  });
  it("checkSelected flags empty picks and an empty selection", () => {
    expect(checkSelected({ must_know: [], should_know: [] }, new Set())).toEqual(["selected nothing"]);
    expect(checkSelected({ must_know: [{ cluster_index: 0, article_ids: [] }], should_know: [] }, new Set())).toEqual(["must_know[0] cites no articles"]);
  });
  it("force replaces; a missing required input is non-retryable", async () => {
    const { store, seen, select } = setup(good);
    store.put(300, SELECT_OUTPUT, '{"must_know":[],"should_know":[]}');
    await select(300, p0, p0, undefined, { force: true });
    expect(seen.n).toBe(1);
    const bare = new ArtifactStore(freshDb([301]));
    await expect(selectActivity({ store: bare, agentsDir: AGENTS })(301, p0, p0)).rejects.toMatchObject({ nonRetryable: true, type: "MissingInput" });
  });
});
