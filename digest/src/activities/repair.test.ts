import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { repairActivity, REPAIR_OUTPUT, type ResolutionDoc } from "./repair.js";
import { describe, expect, it } from "vitest";
import { applyRepairs, buildRepairRequests, resolve, type RepairRequest } from "./repair.js";

const s = (h: string, ids: string[]) => ({ headline: h, summary: "S", why_it_matters: "W", sources: ids.map((article_id) => ({ article_id })) });
const draft = { must_know: [s("Talks resume", ["A1"]), s("Vote passes", ["A2", "A3"])], should_know: [s("Yen jumps", ["A4"])], preheader: "" };

describe("repair", () => {
  it("requests only stories whose every matching failure names repairable fields", () => {
    const report = { results: [
      { headline: "Talks resume", article_ids: ["A1"], pass: false, reason: "summary: 58% absent", failed_fields: ["summary" as const] },
      { headline: "Vote passes", article_ids: ["A3", "A2"], pass: false, reason: "no fields named" },
      { headline: "Yen jumps", article_ids: ["A4"], pass: true, reason: "ok" },
    ] };
    const reqs = buildRepairRequests(draft, report);
    expect(reqs).toEqual([{ article_ids: ["A1"], failed_fields: ["summary"], reason: "summary: 58% absent", fields: { headline: "Talks resume", summary: "S", why_it_matters: "W" } }]);
  });
  const req: RepairRequest = { article_ids: ["A1"], failed_fields: ["summary"], reason: "r", fields: { headline: "H", summary: "S", why_it_matters: "W" } };
  it("patches only exactly the flagged fields, non-empty and free of ids; merges a split story", () => {
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed." }] })[0]).toMatchObject({ status: "recheck_failed", patched_fields: { summary: "Fixed." } });
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed.", headline: "New" }] })[0]?.status).toBe("guard_failed");
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "See (A7)." }] })[0]?.guard).toMatch(/internal id/);
    expect(applyRepairs([req], { results: [] })[0]?.guard).toBe("missing from repaired output");
    const two = { ...req, failed_fields: ["headline", "summary"] as ("headline" | "summary")[] };
    expect(applyRepairs([two], { results: [{ article_ids: ["A1"], headline: "H2" }, { article_ids: ["A1"], summary: "S2" }] })[0]?.status).toBe("recheck_failed");
  });
  it("keeps a patch only on a passed recheck; a contradictory or missing verdict confirms nothing", () => {
    const applied = applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed." }] });
    const scoped = { must_know: [{ ...s("H", ["A1"]), summary: "Fixed." }], should_know: [], preheader: "" };
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }] }, scoped)[0]).toMatchObject({ status: "repaired", recheck_pass: true });
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "H", article_ids: ["A1"], pass: false, reason: "no" }] }, scoped)[0]?.status).toBe("recheck_failed");
    expect(resolve(applied, { results: [] }, scoped)[0]?.status).toBe("recheck_failed");
    // a failure matched only by headline still fails the recheck
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "H", article_ids: [], pass: false, reason: "no", failed_fields: ["summary"] }] }, scoped)[0]?.status).toBe("recheck_failed");
  });
});


const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const ok = (structured: unknown) => ({ type: "result", subtype: "success", result: "", structured_output: structured, total_cost_usd: 0.1, usage: {}, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" }) as unknown as SDKMessage;

// One fake answers both calls: the repairer (whose system prompt is repair.md) and the recheck.
function model(opts: { patch?: unknown; recheckPass?: boolean; fail?: Error }): { q: SdkQuery; calls: string[] } {
  const calls: string[] = [];
  const q = (({ options }: { prompt: string; options?: Options }) => {
    const isRepair = JSON.stringify(options?.systemPrompt ?? "").includes("correction editor");
    calls.push(isRepair ? "repair" : "recheck");
    return (function* () {
      if (opts.fail) throw opts.fail;
      yield isRepair ? ok(opts.patch) : ok({ results: [{ headline: "Deal signed", article_ids: ["A5"], pass: opts.recheckPass ?? true, reason: "r" }] });
    })();
  }) as unknown as SdkQuery;
  return { q, calls };
}

function activitySetup(m: { q: SdkQuery }, signal?: AbortSignal) {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, "articles_1.csv", "article_id,source_id,title,published,summary\nA5,bbc,Deal,2026-09-18,Deal signed on Friday\n");
  const d0 = store.put(300, "draft_s00.json", JSON.stringify({ plan: { index: 0, tier: "must_know", storyIds: ["A5"], contextIds: ["A5"] }, story: s("Deal signed", ["A5"]) }));
  const report = store.put(300, "coherence_report.json", JSON.stringify({ results: [{ headline: "Deal signed", article_ids: ["A5"], pass: false, reason: "summary: Friday absent", failed_fields: ["summary"] }] }));
  const act = repairActivity({ store, agentsDir: AGENTS, query: m.q, maxAttempts: 3, ...(signal ? { signal: () => signal } : {}) });
  return { store, run: () => act(300, [d0], report), doc: () => JSON.parse(store.get(store.find(300, REPAIR_OUTPUT)!)) as ResolutionDoc };
}

describe("repair activity", () => {
  it("repairs, rechecks, and records a repaired verdict", async () => {
    const m = model({ patch: { results: [{ article_ids: ["A5"], summary: "Deal signed.", action: "deleted_unsupported" }] } });
    const { run, doc } = activitySetup(m);
    await run();
    expect(m.calls).toEqual(["repair", "recheck"]);
    expect(doc().results[0]).toMatchObject({ status: "repaired", recheck_pass: true, patched_fields: { summary: "Deal signed." } });
  });
  it("a failed recheck keeps nothing", async () => {
    const { run, doc } = activitySetup(model({ patch: { results: [{ article_ids: ["A5"], summary: "Deal signed." }] }, recheckPass: false }));
    await run();
    expect(doc().results[0]?.status).toBe("recheck_failed");
  });
  it("an aborted activity rethrows and stores nothing", async () => {
    const ac = new AbortController();
    ac.abort();
    const { store, run } = activitySetup(model({ fail: new Error("AbortError") }), ac.signal);
    await expect(run()).rejects.toThrow(/AbortError/);
    expect(store.find(300, REPAIR_OUTPUT)).toBeUndefined();
  });
  it("a fault on the last attempt is recorded, and a cached fault is rerun rather than reused", async () => {
    const { run, doc } = activitySetup(model({ fail: new Error("529 overloaded") }));
    await run(); // outside an activity every attempt is the last
    expect(doc().fault).toMatch(/529/);
    expect(doc().results.every((r) => r.status !== "repaired")).toBe(true);
  });
  it("a cached fault is rerun rather than reused", async () => {
    const first = activitySetup(model({ fail: new Error("529 overloaded") }));
    await first.run();
    const m2 = model({ patch: { results: [{ article_ids: ["A5"], summary: "Deal signed." }] } });
    const drafts = [first.store.find(300, "draft_s00.json")!];
    const report = first.store.find(300, "coherence_report.json")!;
    await repairActivity({ store: first.store, agentsDir: AGENTS, query: m2.q, maxAttempts: 3 })(300, drafts, report);
    expect(m2.calls).toEqual(["repair", "recheck"]);
    expect(first.doc().results[0]?.status).toBe("repaired");
    expect(first.store.find(300, `${REPAIR_OUTPUT}.corrupt.1`)).toBeDefined();
  });
});
