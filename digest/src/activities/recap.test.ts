import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { recapActivity, RECAP_INPUT, RECAP_OUTPUT } from "./recap.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;

function fakeQuery(text: string, calls: { n: number; prompt?: string; options?: Options }): SdkQuery {
  return (({ prompt, options }: { prompt: string; options?: Options }) => {
    calls.n++;
    calls.prompt = prompt;
    if (options) calls.options = options;
    return (function* () {
      yield { type: "result", subtype: "success", result: text, total_cost_usd: 0.01, usage: {}, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}

function setup(recapText = "A quiet week of steady themes.") {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, RECAP_INPUT, "title,date\nTalks resume,2026-09-17\n");
  const calls: { n: number; prompt?: string; options?: Options } = { n: 0 };
  const beats: number[] = [];
  const recap = recapActivity({ store, agentsDir: AGENTS, query: fakeQuery(recapText, calls), heartbeat: () => beats.push(1) });
  return { store, calls, beats, recap };
}

describe("recap activity", () => {
  it("produces recap.txt from the titles CSV inline, with no tools, on the run's date, and heartbeats", async () => {
    const { store, calls, beats, recap } = setup();
    const p = await recap(300);
    expect(p.name).toBe(RECAP_OUTPUT);
    expect(store.get(p)).toBe("A quiet week of steady themes.");
    expect(calls.n).toBe(1);
    expect(calls.prompt).toContain("Talks resume,2026-09-17");
    expect(calls.options?.tools).toEqual([]);
    expect(calls.options?.model).toBe("claude-haiku-4-5");
    expect(calls.options?.systemPrompt).not.toContain("Read tool");
    expect(beats.length).toBe(2);
  });
  it("is idempotent on output: a valid archived recap is returned without a model call", async () => {
    const { store, calls, recap } = setup();
    const first = await recap(300);
    expect(await recap(300)).toEqual(first);
    expect(calls.n).toBe(1);
    expect(store.find(300, RECAP_OUTPUT)).toEqual(first);
  });
  it("quarantines an archived recap that fails the validator and produces a fresh one", async () => {
    const { store, calls, recap } = setup();
    store.put(300, RECAP_OUTPUT, "   ");
    const p = await recap(300);
    expect(store.get(p)).toBe("A quiet week of steady themes.");
    expect(store.find(300, `${RECAP_OUTPUT}.corrupt.1`)).toBeDefined();
    expect(calls.n).toBe(1);
  });
  it("force replaces a valid archived recap with a fresh sample", async () => {
    const { store, calls, recap } = setup();
    store.put(300, RECAP_OUTPUT, "the old recap");
    const p = await recap(300, true);
    expect(store.get(p)).toBe("A quiet week of steady themes.");
    expect(calls.n).toBe(1);
  });
  it("a missing input is a non-retryable failure, and an empty model reply stores nothing", async () => {
    const { store: s2, recap: r2 } = setup("   ");
    await expect(r2(300)).rejects.toThrow(/empty recap/);
    expect(s2.find(300, RECAP_OUTPUT)).toBeUndefined();
    const store = new ArtifactStore(freshDb([301]));
    const recap = recapActivity({ store, agentsDir: AGENTS, query: fakeQuery("x", { n: 0 }) });
    await expect(recap(301)).rejects.toMatchObject({ nonRetryable: true, type: "MissingInput" });
  });
});
