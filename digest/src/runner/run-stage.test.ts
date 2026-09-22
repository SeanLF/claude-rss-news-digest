import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { StageSpec } from "./prompt.js";
import { runStage, type SdkQuery } from "./run-stage.js";

const spec: StageSpec = { name: "coherence", model: "claude-sonnet-5", thinking: "adaptive", tools: ["Read", "Grep"], body: "Reply with JSON." };

// A fake with the SDK's own signature, so a renamed option or message field fails to compile
// in the adapter; the casts are confined to these fakes.
function fakeQuery(messages: SDKMessage[], seen?: { options?: Options }): SdkQuery {
  return (({ options }: { prompt: string; options?: Options }) => {
    if (seen && options) seen.options = options;
    return (function* () {
      yield* messages;
    })();
  }) as unknown as SdkQuery;
}
const result = (over: Record<string, unknown>): SDKMessage =>
  ({ type: "result", subtype: "success", result: "{}", total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: false, num_turns: 1, session_id: "s", ...over }) as unknown as SDKMessage;
const assistant = (content: unknown[]): SDKMessage => ({ type: "assistant", message: { content }, session_id: "s" }) as unknown as SDKMessage;
const call = (q: SdkQuery, schema?: Record<string, unknown>) =>
  runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, ...(schema ? { outputSchema: schema } : {}) });

describe("runStage", () => {
  it("collects tool calls in order, the final text, the structured output and the numbers", async () => {
    const q = fakeQuery([
      assistant([{ type: "tool_use", id: "1", name: "Read", input: { file_path: "/in/a.csv" } }, { type: "tool_use", id: "2", name: "Grep", input: { pattern: "58%", path: "/in" } }]),
      result({ result: '{"results":[]}', structured_output: { results: [] }, total_cost_usd: 0.5, usage: { output_tokens: 10, server_tool_use: { x: 1 } }, duration_ms: 1200, num_turns: 3 }),
    ]);
    const r = await call(q, { type: "object" });
    expect(r.toolCalls).toEqual([{ name: "Read", target: "/in/a.csv" }, { name: "Grep", target: "58%" }]);
    expect(r.text).toBe('{"results":[]}');
    expect(r.structured).toEqual({ results: [] });
    expect(r).toMatchObject({ costUsd: 0.5, usage: { output_tokens: 10 }, durationMs: 1200, numTurns: 3 });
  });
  it("a stage with no tools gets an empty base set: no built-in reaches the model", async () => {
    const seen: { options?: Options } = {};
    await runStage({ ...spec, tools: [] }, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: fakeQuery([result({})], seen) });
    expect(seen.options?.tools).toEqual([]);
    expect(seen.options?.disallowedTools).toEqual(expect.arrayContaining(["Read", "Grep", "Bash"]));
  });
  it("passes model, cwd, thinking, the tool base set, allowed and disallowed tools, and the schema through to the SDK options", async () => {
    const seen: { options?: Options } = {};
    await call(fakeQuery([result({ structured_output: {} })], seen), { type: "object" });
    expect(seen.options?.model).toBe("claude-sonnet-5");
    expect(seen.options?.cwd).toBe("/in");
    expect(seen.options?.thinking).toEqual({ type: "adaptive" });
    expect(seen.options?.tools).toEqual(["Read", "Grep"]);
    expect(seen.options?.allowedTools).toEqual(["Read", "Grep"]);
    expect(seen.options?.disallowedTools).toEqual(expect.arrayContaining(["Write", "Edit", "Bash", "WebFetch", "WebSearch"]));
    expect(seen.options?.disallowedTools).not.toContain("Read");
    expect(seen.options?.outputFormat).toEqual({ type: "json_schema", schema: { type: "object" } });
    expect(seen.options?.settings).toEqual({ permissions: { blockReadsOutsideWorkingDirectories: true } });
    expect(seen.options?.maxBudgetUsd).toBeUndefined();
  });
  it("heartbeats on every streamed message", async () => {
    let beats = 0;
    await runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: fakeQuery([assistant([]), assistant([]), result({})]), heartbeat: () => beats++ });
    expect(beats).toBe(3);
  });
  it("omits outputFormat when no schema is requested and falls back to the assistant text", async () => {
    const seen: { options?: Options } = {};
    const r = await call(fakeQuery([assistant([{ type: "text", text: "plain" }]), result({ result: "" })], seen));
    expect(seen.options?.outputFormat).toBeUndefined();
    expect(r.text).toBe("plain");
    expect("structured" in r).toBe(false);
  });
  it("throws when a schema was requested and the success carries no structured output", async () => {
    await expect(call(fakeQuery([result({})]), { type: "object" })).rejects.toThrow(/structured/);
  });
  it("throws on a non-success result and on a stream with no result, never returning partial output", async () => {
    await expect(call(fakeQuery([result({ subtype: "error_max_turns", is_error: true })]))).rejects.toThrow(/error_max_turns/);
    await expect(call(fakeQuery([assistant([{ type: "text", text: "partial" }])]))).rejects.toThrow(/no result/);
  });
});
