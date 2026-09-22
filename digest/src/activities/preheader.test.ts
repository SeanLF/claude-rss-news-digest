import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { cleanPreheader, preheaderActivity, preheaderLine, truncateOnWordBoundary } from "./preheader.js";

describe("cleanPreheader", () => {
  it("strips a known label, a list marker and wrapping quotes, keeps the first usable line", () => {
    expect(cleanPreheader("Preheader: Iran strikes widen; Manila counts quake dead\nsecond")).toBe("Iran strikes widen; Manila counts quake dead");
    expect(cleanPreheader('**Preheader:**\n- "Talks stall in Geneva"')).toBe("Talks stall in Geneva");
    expect(cleanPreheader("```\n```")).toBe("");
  });
  it("never decapitates a lead clause that looks like a label", () => {
    expect(cleanPreheader("WHO: companies filed 235 lawsuits")).toBe("WHO: companies filed 235 lawsuits");
  });
  it("truncates on a word boundary with an ellipsis, within the cap", () => {
    expect(truncateOnWordBoundary("alpha beta gamma", 12)).toBe("alpha beta…");
    expect(truncateOnWordBoundary("😀😀😀😀😀", 3)).toBe("😀😀…");
    const long = cleanPreheader("word ".repeat(60));
    expect(long.length).toBeLessThanOrEqual(157);
    expect(long.endsWith("word…")).toBe(true);
  });
});

describe("preheader activity", () => {
  it("sends the headlines by tier, cleans the reply, and stores it", async () => {
    const store = new ArtifactStore(freshDb([300]));
    const d0 = store.put(300, "draft_s00.json", JSON.stringify({ plan: { index: 0, tier: "must_know", storyIds: ["A1"], contextIds: ["A1"] }, story: { headline: "Russia votes", summary: "s", sources: [] } }));
    const d1 = store.put(300, "draft_s01.json", JSON.stringify({ plan: { index: 1, tier: "should_know", storyIds: ["A2"], contextIds: ["A2"] }, story: { headline: "Yen jumps", summary: "s", sources: [] } }));
    const seen: { prompt?: string; options?: Options } = {};
    const q = (({ prompt, options }: { prompt: string; options?: Options }) => {
      seen.prompt = prompt;
      if (options) seen.options = options;
      return (function* () {
        yield { type: "result", subtype: "success", result: "Preheader: Russia votes as the yen jumps", total_cost_usd: 0.01, usage: {}, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
      })();
    }) as unknown as SdkQuery;
    const p = await preheaderActivity({ store, agentsDir: new URL("../../agents/", import.meta.url).pathname, query: q })(300, [d0, d1]);
    expect(preheaderLine(store.get(p))).toBe("Russia votes as the yen jumps");
    expect(JSON.parse(seen.prompt ?? "{}")).toEqual({ must_know: [{ headline: "Russia votes" }], should_know: [{ headline: "Yen jumps" }] });
    expect(seen.options?.tools).toEqual([]);
    // rewritten drafts mean a new preheader, never the stale one
    const d2 = store.put(300, "draft_s02.json", JSON.stringify({ plan: { index: 0, tier: "must_know", storyIds: ["A9"], contextIds: ["A9"] }, story: { headline: "Deal signed", summary: "s", sources: [] } }));
    await preheaderActivity({ store, agentsDir: new URL("../../agents/", import.meta.url).pathname, query: q })(300, [d2]);
    expect(store.find(300, "preheader.json.corrupt.1")).toBeDefined();
  });
});
