import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { assemble, clusterFor } from "./assemble.js";

const story = (h: string, ids: string[], extra: Record<string, unknown> = {}) => ({ headline: h, summary: "S", why_it_matters: "W", sources: ids.map((article_id) => ({ article_id })), ...extra });
function setup(results: unknown[], resolution: unknown[] = [], preheader: string | null = "Pre") {
  const store = new ArtifactStore(freshDb([300]));
  store.put(300, "clusters.json", JSON.stringify({ clusters: [{ story: "Russia votes", article_ids: ["A1", "A2"] }, { story: "Yen", article_ids: ["A4"] }] }));
  store.put(300, "selected.json", JSON.stringify({ must_know: [], should_know: [], not_covered_blurb: "Held back a Balkan ruling." }));
  const plans = [["must_know", "Russia votes", ["A1", "A2"], { reporting_varies: [{ source: "NYT (A316)", angle: "a", bias: "center" }] }], ["must_know", "Talks stall", ["A3"], {}], ["must_know", "Deal signed", ["A5"], {}], ["should_know", "Yen jumps", ["A4"], {}]] as const;
  const drafts = plans.map(([tier, h, ids, extra], i) => store.put(300, `draft_s0${i}.json`, JSON.stringify({ plan: { index: i, tier, storyIds: ids, contextIds: ids }, story: story(h, [...ids], extra) })));
  const report = store.put(300, "coherence_report.json", JSON.stringify({ results }));
  const repair = store.put(300, "repair_resolution.json", JSON.stringify({ input: "x", results: resolution }));
  const pre = preheader === null ? null : store.put(300, "preheader.txt", preheader);
  return () => assemble(store, 300, drafts, report, repair, pre);
}
const pass = (h: string, ids: string[]) => ({ headline: h, article_ids: ids, pass: true, reason: "ok" });
const fail = (h: string, ids: string[]) => ({ headline: h, article_ids: ids, pass: false, reason: "s", failed_fields: ["summary"] });

describe("assemble", () => {
  it("keeps passes, blanks a why-only fail, applies a confirmed repair, drops the rest, scrubs and labels", () => {
    const run = setup(
      [pass("Russia votes", ["A1", "A2"]), { headline: "Talks stall", article_ids: ["A3"], pass: false, reason: "why", failed_fields: ["why_it_matters"] }, { headline: "Deal signed", article_ids: ["A5"], pass: false, reason: "summary", failed_fields: ["summary"] }, { headline: "Yen jumps", article_ids: ["A4"], pass: false, reason: "why", failed_fields: ["why_it_matters"] }],
      [{ article_ids: ["A5"], status: "repaired", recheck_pass: true, patched_fields: { summary: "Fixed." } }],
    );
    const { selections, report } = run();
    expect(selections.must_know.map((s) => [s.headline, s.why_it_matters, s.cluster_id])).toEqual([["Russia votes", "W", "Russia votes"], ["Talks stall", "", undefined], ["Deal signed", "W", undefined]]);
    expect(selections.must_know[2]?.summary).toBe("Fixed.");
    expect(selections.must_know[0]?.reporting_varies).toEqual([{ source: "NYT", angle: "a", bias: "center" }]);
    expect(selections.should_know).toEqual([{ headline: "Yen jumps", summary: "S", sources: [{ article_id: "A4" }], cluster_id: "Yen" }]);
    expect(selections.preheader).toBe("Pre");
    expect(selections.not_covered_blurb).toBe("Held back a Balkan ruling.");
    expect(report).toEqual({ shipped: 4, dropped: [], repaired: 1, blanked: 1 });
  });
  it("drops an unrepaired non-why failure and a repair not confirmed by the recheck", () => {
    const { selections, report } = setup([pass("Russia votes", ["A1", "A2"]), pass("Talks stall", ["A3"]), { headline: "Deal signed", article_ids: ["A5"], pass: false, reason: "s", failed_fields: ["summary"] }, pass("Yen jumps", ["A4"])], [{ article_ids: ["A5"], status: "recheck_failed", recheck_pass: false, patched_fields: { summary: "x" } }])();
    expect(report.dropped).toEqual(["Deal signed"]);
    expect(selections.must_know).toHaveLength(2);
  });
  it("fills an empty preheader from the top headline, and refuses an empty digest", () => {
    expect(setup([pass("Russia votes", ["A1", "A2"])], [], null)().selections.preheader).toBe("Russia votes");
    expect(() => setup([fail("Russia votes", ["A1", "A2"]), fail("Talks stall", ["A3"]), fail("Deal signed", ["A5"])])()).toThrow(/no must_know story survived/);
  });
  it("clusterFor votes by distinct ids, earliest on a tie", () => {
    const owner = new Map([["A1", "x"], ["A2", "y"], ["A3", "y"]]);
    expect(clusterFor(["A1", "A1", "A2", "A3"], owner)).toBe("y");
    expect(clusterFor(["A1", "A2"], owner)).toBe("x");
  });
});
