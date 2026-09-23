import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { freshDb } from "../store/test-db.js";
import { WEEKLY_RECAP, weeklyRecapActivity } from "./weekly-recap.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
type Calls = { n: number; prompt?: string; options?: Options };
function fakeQuery(reply: string | Error, calls: Calls): SdkQuery {
  return (({ prompt, options }: { prompt: string; options?: Options }) => {
    calls.n++;
    calls.prompt = prompt;
    if (options) calls.options = options;
    return (function* () {
      if (reply instanceof Error) throw reply;
      yield { type: "result", subtype: "success", result: reply, total_cost_usd: 0.001, usage: {}, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}

// Run 300 on 2026-09-18 and run 301 on 2026-09-25; shown titles in the week before run 301.
async function setup(prior: string | null) {
  const path = await freshDb([300, 301]);
  const db = openDb(path);
  await db.run("UPDATE digest_runs SET run_at='2026-09-25 10:25:00' WHERE id=301");
  for (const [h, t, at] of [["Editorial headline", "Ceasefire talks resume", "2026-09-24 10:40:00"], ["Another", null, "2026-09-20 10:40:00"], ["Too old", null, "2026-09-10 10:40:00"], ["Written after the run", null, "2026-09-25 11:00:00"]])
    await db.run("INSERT INTO shown_narratives (headline, tier, original_title, shown_at) VALUES ($1, 'must_know', $2, $3)", [h, t, at]);
  const store = new ArtifactStore(path);
  if (prior !== null) await store.put(300, WEEKLY_RECAP, prior);
  return { store, path };
}
const week = (d: string, text = `Themes of ${d}.`) => `## Week of ${d}\n${text}\n\n`;
const deps = (s: Awaited<ReturnType<typeof setup>>, query: SdkQuery, maxAttempts = 3) => ({ store: s.store, dbUrl: s.path, agentsDir: AGENTS, query, maxAttempts });
afterEach(async () => {
  vi.restoreAllMocks();
});

describe("weekly recap activity", () => {
  it("a week-old recap gains this week's entry from the titles shown before the run, and the run stores it", async () => {
    const s = await setup(week("2026-09-11") + week("2026-09-18"));
    const calls: Calls = { n: 0 };
    const p = await weeklyRecapActivity(deps(s, fakeQuery("  A week of talks.  ", calls)))(301);
    expect(p?.name).toBe(WEEKLY_RECAP);
    expect(await s.store.get(p!)).toBe(week("2026-09-11") + week("2026-09-18") + week("2026-09-25", "A week of talks."));
    expect(calls.prompt).toBe("- Ceasefire talks resume\n- Another");
    expect(calls.options?.model).toBe("claude-haiku-4-5");
    expect(calls.options?.tools).toEqual([]);
  });
  it("a recap under a week old is carried forward unchanged, with no model call", async () => {
    const s = await setup(week("2026-09-19"));
    const calls: Calls = { n: 0 };
    const p = await weeklyRecapActivity(deps(s, fakeQuery("x", calls)))(301);
    expect(await s.store.get(p!)).toBe(week("2026-09-19"));
    expect(calls.n).toBe(0);
  });
  it("keeps the last six weeks", async () => {
    const six = ["2026-08-14", "2026-08-21", "2026-08-28", "2026-09-04", "2026-09-11", "2026-09-18"].map((d) => week(d)).join("");
    const s = await setup(six);
    const p = await weeklyRecapActivity(deps(s, fakeQuery("New.", { n: 0 })))(301);
    const kept = [...(await s.store.get(p!)).matchAll(/^## Week of (\S+)/gm)].map((m) => m[1]);
    expect(kept).toEqual(["2026-08-21", "2026-08-28", "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25"]);
  });
  it("starts the file when no earlier run has one", async () => {
    const s = await setup(null);
    const p = await weeklyRecapActivity(deps(s, fakeQuery("First.", { n: 0 })))(301);
    expect(await s.store.get(p!)).toBe(week("2026-09-25", "First."));
  });
  it("is idempotent on output: a run that already has its recap makes no call", async () => {
    const s = await setup(week("2026-09-11"));
    const calls: Calls = { n: 0 };
    const recap = weeklyRecapActivity(deps(s, fakeQuery("Once.", calls)));
    const first = await recap(301);
    expect(await recap(301)).toEqual(first);
    expect(calls.n).toBe(1);
  });
  it("a failed call throws while attempts remain, and on the last carries the old recap forward", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const s = await setup(week("2026-09-11"));
    const recap = weeklyRecapActivity({ ...deps(s, fakeQuery(new Error("overloaded"), { n: 0 }), 1) });
    const p = await recap(301);
    expect(await s.store.get(p!)).toBe(week("2026-09-11"));
    expect(String(warn.mock.calls[0]?.[0])).toContain("Weekly recap generation failed (non-fatal)");
    const again = await setup(week("2026-09-11"));
    await expect(weeklyRecapActivity({ ...deps(again, fakeQuery(new Error("overloaded"), { n: 0 }), 3), attempt: () => 1 })(301)).rejects.toThrow("overloaded");
  });
  it("with nothing shown in the week and no earlier recap, stores nothing", async () => {
    const s = await setup(null);
    await openDb(s.path).exec("DELETE FROM shown_narratives");
    expect(await weeklyRecapActivity(deps(s, fakeQuery("x", { n: 0 })))(301)).toBeNull();
  });
});
