import type { UsageRow } from "../store/usage.js";
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { buildInlineGrepBody, buildReadLoopBody, coherenceActivity, COHERENCE_OUTPUT, DRAFT_OUTPUT, unbackedFails } from "./coherence.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
// digest/agents/coherence.md carries the production body verbatim; a host-only test holds the two equal.
const PROD = new URL("../../agents/coherence.md", import.meta.url).pathname;

describe("buildInlineGrepBody", () => {
  const body = readFileSync(PROD, "utf8").split("---").slice(2).join("---").trim();
  it("changes only steps 1 and 3 and the tools rule; step 2 and the probes survive byte for byte", () => {
    const out = buildInlineGrepBody(body, "/in");
    expect(out).toContain("Your input arrives in the next message, inline");
    expect(out).toContain("under `/in/`");
    expect(out).toContain("2. For each story in draft_selections.json");
    expect(out).toContain("before you FAIL a field, use the Grep tool");
    expect(out).not.toContain("Use the Write tool");
    const probes = body.slice(body.indexOf("**For each field, run all three probes"), body.indexOf("**Rules:**"));
    expect(out).toContain(probes);
  });
  it("the read-loop body keeps the probes, drops Write, and reads from the working directory", () => {
    const out = buildReadLoopBody(body);
    expect(out).toContain("Reply with the JSON object and nothing else");
    expect(out).not.toContain("Use the Write tool");
    expect(out).not.toContain("/app/data/claude_input/");
    const probes = body.slice(body.indexOf("**For each field, run all three probes"), body.indexOf("**Rules:**"));
    expect(out).toContain(probes);
  });
  it("refuses a drifted prompt", () => {
    expect(() => buildInlineGrepBody(body.replace("2. For each story in draft_selections.json", "2. For every story"), "/in")).toThrow(/drifted/);
  });
});

const story = (h: string, ids: string[]) => ({ headline: h, summary: "S", why_it_matters: "W", sources: ids.map((article_id) => ({ article_id })) });

function setup(report: unknown, toolCalls: { name: string; target: string }[] = []) {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, "articles_1.csv", "article_id,source_id,title,published,summary\nA1,hn,T,2026-09-18,https://x.com/y\nA2,bbc,T2,2026-09-18,s\n");
  const d0 = store.put(300, "draft_s00.json", JSON.stringify({ plan: { index: 0, tier: "must_know", storyIds: ["A1"], contextIds: ["A1"] }, story: story("Talks resume", ["A1"]) }));
  const d1 = store.put(300, "draft_s01.json", JSON.stringify({ plan: { index: 1, tier: "should_know", storyIds: ["A2"], contextIds: ["A2"] }, story: story("Yen jumps", ["A2"]) }));
  const seen: { n: number; prompt?: string; options?: Options } = { n: 0 };
  const q = (({ prompt, options }: { prompt: string; options?: Options }) => {
    seen.n++;
    seen.prompt = prompt;
    if (options) seen.options = options;
    return (function* () {
      yield { type: "assistant", message: { content: toolCalls.map((t, i) => ({ type: "tool_use", id: String(i), name: t.name, input: { pattern: t.target } })) }, session_id: "s" } as unknown as SDKMessage;
      yield { type: "result", subtype: "success", result: "", structured_output: report, total_cost_usd: 0.9, usage: {}, duration_ms: 5, is_error: false, num_turns: 4, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
  const rows: UsageRow[] = [];
  const act = coherenceActivity({ store, agentsDir: AGENTS, query: q, onUsage: (r) => rows.push(r) });
  return { store, seen, act, drafts: [d0, d1], rows };
}
const ft = { runId: 300, name: "article_fulltext.json", sha256: "0".repeat(64) };

describe("coherence activity", () => {
  it("inlines the draft and corpus with links scrubbed, stores the assembled draft and the covered report", async () => {
    const report = { results: [{ headline: "Talks resume", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "x", article_ids: ["A2"], pass: false, reason: "summary: 58% absent", failed_fields: ["summary"], failure_kinds: { summary: "unsupported" } }] };
    const { store, seen, act, drafts, rows } = setup(report, [{ name: "Grep", target: "58%" }]);
    const p = await act(300, drafts, ft);
    expect(JSON.parse(store.get(p))).toEqual(report);
    const draft = JSON.parse(store.get(store.find(300, DRAFT_OUTPUT)!)) as { must_know: { headline: string }[]; should_know: { headline: string; why_it_matters?: string }[] };
    expect(draft.must_know.map((s) => s.headline)).toEqual(["Talks resume"]);
    expect(draft.should_know.map((s) => s.headline)).toEqual(["Yen jumps"]);
    expect(seen.prompt).toContain("## draft_selections.json");
    expect(seen.prompt).toContain("[link]");
    expect(seen.prompt).not.toContain("https://");
    expect(seen.options?.tools).toEqual(["Read", "Grep"]);
    expect(rows[0]?.unbackedFails).toBe(1); // "58%" is under four characters, so it backs nothing
    expect(await act(300, drafts, ft)).toEqual(p);
    expect(seen.n).toBe(1);
  });
  it("a matching stored draft survives a missing report and is not quarantined", async () => {
    const report = { results: [{ headline: "Talks resume", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "Yen jumps", article_ids: ["A2"], pass: true, reason: "ok" }] };
    const { store, act, drafts } = setup(report);
    await act(300, drafts, ft);
    store.quarantine(300, COHERENCE_OUTPUT);
    await act(300, drafts, ft);
    expect(store.find(300, `${DRAFT_OUTPUT}.corrupt.1`)).toBeUndefined();
    expect(store.find(300, COHERENCE_OUTPUT)).toBeDefined();
  });
  it("a report that leaves a story unchecked is a failure and stores nothing", async () => {
    const { store, act, drafts } = setup({ results: [{ headline: "Talks resume", article_ids: ["A1"], pass: true, reason: "ok" }] });
    await expect(act(300, drafts, ft)).rejects.toThrow(/no result matches 1 draft story\(ies\): Yen jumps/);
    expect(store.find(300, COHERENCE_OUTPUT)).toBeUndefined();
  });
  it("unbackedFails counts fails whose reason quotes no Grep pattern of four or more characters", () => {
    const r = { results: [{ headline: "a", article_ids: [], pass: false, reason: "tenure absent" }, { headline: "b", article_ids: [], pass: false, reason: "58% absent" }] };
    expect(unbackedFails(r, [{ name: "Grep", target: "tenure" }, { name: "Grep", target: "58%" }])).toBe(1);
  });
});
