import { describe, expect, it } from "vitest";
import { asIndex, parseLinks, selectedLabels, validateLinks } from "./link.js";

// Cases carried from newsroom/tests/test_threads.py.
const clusters = (...groups: [string, string[]][]) => ({ clusters: groups.map(([story, article_ids]) => ({ story, article_ids })) });
const must = (...picks: object[]) => ({ must_know: picks, should_know: [] });

describe("selectedLabels", () => {
  it("labels from the pick's article ids, not a wrong cluster index", () => {
    expect(selectedLabels(clusters(["wrong story", ["A1"]], ["filler", ["A2"]], ["real story", ["A5", "A6", "A7"]]), must({ cluster_index: 0, article_ids: ["A5", "A6", "A7"] })).map((e) => e.story)).toEqual(["real story"]);
  });
  it("takes the plurality when ids span clusters, counting each article once", () => {
    expect(selectedLabels(clusters(["minority", ["A1", "A2"]], ["majority", ["A5", "A6", "A7"]]), must({ cluster_index: 0, article_ids: ["A1", "A5", "A6", "A7"] }))[0]!.story).toBe("majority");
    expect(selectedLabels(clusters(["own", ["A1", "A2", "A3"]], ["other", ["A9"]]), must({ article_ids: ["A1", "A2", "A3", "A9", "A9", "A9", "A9"] }))[0]!.story).toBe("own");
  });
  it("skips a pick whose ids map nowhere; uses the index only for a pick with no ids", () => {
    expect(selectedLabels(clusters(["indexed story", ["A1"]]), must({ cluster_index: 0, article_ids: ["A999"] }))).toEqual([]);
    expect(selectedLabels(clusters(["indexed story", ["A1", "A2"]]), must({ cluster_index: 0 }))).toEqual([{ story: "indexed story", tier: "must_know", article_ids: ["A1", "A2"] }]);
  });
  it("ignores non-string ids, and keeps the pick's own ids", () => {
    expect(selectedLabels(clusters(["real story", ["A5"]]), must({ cluster_index: 0, article_ids: [["A9"], "A5"] }))[0]!.story).toBe("real story");
    expect(selectedLabels(clusters(["real story", ["A5", "A6", "A7"]]), must({ cluster_index: 99, article_ids: ["A5", "A6"] }))[0]!.article_ids).toEqual(["A5", "A6"]);
  });
  it("orders must_know before should_know", () => {
    const out = selectedLabels(clusters(["a", ["A1"]], ["b", ["A2"]]), { should_know: [{ article_ids: ["A1"] }], must_know: [{ article_ids: ["A2"] }] });
    expect(out.map((e) => [e.story, e.tier])).toEqual([["b", "must_know"], ["a", "should_know"]]);
  });
});

describe("validateLinks", () => {
  const active = [{ thread_id: 7, label: "x", recent_labels: ["x"] }];
  it("keeps in-range stories linked to offered threads and counts the rest as refused", () => {
    const { mapping, health } = validateLinks({ links: [{ story: 0, thread: 7 }, { story: 1, thread: 99 }, { story: 9, thread: 7 }, { story: 2, thread: null }] }, active, 3);
    expect(mapping).toEqual([7, null, null]);
    expect(health).toEqual({ ok: true, proposed: 3, validated: 1 });
  });
  it("a genuinely all-new answer proposes nothing", () => {
    expect(validateLinks({ links: [{ story: 0, thread: null }] }, active, 1).health).toEqual({ ok: true, proposed: 0, validated: 0 });
  });
});

describe("parseLinks", () => {
  it("reads the links out of fences and prose, a quoted id as the number", () => {
    expect(parseLinks('Here you go:\n```json\n{"links": [{"story": 0, "thread": "261"}, {"story": "1", "thread": null}, {"story": 2, "thread": "NEW"}]}\n```')).toEqual({ links: [{ story: 0, thread: 261 }, { story: 1, thread: null }, { story: 2, thread: null }] });
  });
  it("throws on an answer with nothing to read, so the attempt is re-sampled", () => {
    expect(() => parseLinks("I cannot help with that.")).toThrow(/no parseable links/);
    expect(() => parseLinks('{"links": {"0": 3}}')).toThrow(/no parseable links/);
  });
  it.each([[1.5, null], [true, null], ["-5", null], [" 7 ", 7], [null, null]])("asIndex(%j) is %j", (v, want) => {
    expect(asIndex(v)).toBe(want);
  });
});
