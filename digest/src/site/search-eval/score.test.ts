import { describe, expect, it } from "vitest";
import { type Hit, blindPool, labelKey, permuteLabels, scoreQuery, summarise } from "./score.js";

const h = (headline: string, date: string | null = "2026-09-01"): Hit => ({ headline, date });
const labels = (q: string, rel: Record<string, boolean>) => new Map(Object.entries(rel).map(([hl, v]) => [labelKey(q, hl), v]));
const NOW = "2026-09-23";

describe("scoreQuery", () => {
  it("counts relevant stories among the first ten distinct stories over min(10, R)", () => {
    const l = labels("q", { a: true, b: false, c: true });
    // a twice (one story, two sources), then b, c: 2 relevant stories, R = 2.
    const s = scoreQuery([h("a"), h("a"), h("b"), h("c")], [], l, "q", 2, NOW);
    expect(s.rel).toBe(1);
    expect(s.dupSlots).toBe(1);
    expect(s.unjudged).toBe(0);
  });

  it("gives nothing for a relevant story past the tenth distinct story", () => {
    const l = labels("q", { x: false, a: true });
    const tenOthers = Array.from({ length: 10 }, (_, i) => h("x", `2026-01-${10 + i}`));
    expect(scoreQuery([...tenOthers, h("a")], [], l, "q", 1, NOW).rel).toBe(0);
  });

  it("reads past duplicate rows: ranking is scored apart from dedup", () => {
    const l = labels("q", { a: true, b: true });
    const s = scoreQuery([...Array.from({ length: 12 }, () => h("a")), h("b")], [], l, "q", 2, NOW);
    expect(s.rel).toBe(1);
    expect(s.dupSlots).toBe(9);
  });

  it("treats the same headline on two dates as two stories", () => {
    const l = labels("q", { a: true });
    expect(scoreQuery([h("a", "2026-09-01"), h("a", "2026-09-02")], [], l, "q", 2, NOW).rel).toBe(1);
  });

  it("counts unjudged stories rather than scoring them", () => {
    const s = scoreQuery([h("a"), h("zz")], [], labels("q", { a: true }), "q", 1, NOW);
    expect(s.unjudged).toBe(1);
  });

  it("measures overlap against the reference's distinct first-ten headlines", () => {
    const ref = [h("a"), h("a"), h("b"), h("c"), h("d")];
    const s = scoreQuery([h("b"), h("x"), h("a")], ref, new Map(), "q", 0, NOW);
    expect(s.overlap).toBe(0.5);
    expect(scoreQuery([h("a")], [], new Map(), "q", 0, NOW).overlap).toBeNull();
  });

  it("takes the median age in days of the distinct stories shown", () => {
    const s = scoreQuery([h("a", "2026-09-22"), h("a", "2026-09-22"), h("b", "2026-09-13"), h("c", "2026-08-24"), h("d", null)], [], new Map(), "q", 0, NOW);
    expect(s.medianAgeDays).toBe(10);
    expect(scoreQuery([], [], new Map(), "q", 0, NOW).medianAgeDays).toBeNull();
  });

  it("leaves rel undefined for a query with nothing relevant in the pool", () => {
    expect(scoreQuery([h("a")], [], labels("q", { a: false }), "q", 0, NOW).rel).toBeNull();
  });
});

describe("summarise", () => {
  it("averages over the queries that define each metric and counts empty answers", () => {
    const s = summarise([
      { q: "a", rel: 1, overlap: 1, medianAgeDays: 10, dupSlots: 2, unjudged: 0, empty: false },
      { q: "b", rel: 0.5, overlap: null, medianAgeDays: 30, dupSlots: 0, unjudged: 1, empty: false },
      { q: "c", rel: null, overlap: 0, medianAgeDays: null, dupSlots: 0, unjudged: 0, empty: true },
    ]);
    expect(s).toEqual({ rel: 0.75, overlap: 0.5, medianAgeDays: 20, dupSlots: 2, unjudged: 1, zero: 1 });
  });
});

describe("the blind pool", () => {
  it("holds each (query, headline) once, shuffled, with no trace of which system returned it", () => {
    const pool = blindPool({ sysA: { q: [h("a"), h("b")] }, sysB: { q: [h("b"), h("c")], r: [h("a")] } }, 7);
    expect(pool.map((p) => `${p.query}/${p.headline}`).toSorted()).toEqual(["q/a", "q/b", "q/c", "r/a"]);
    expect(Object.keys(pool[0]!).toSorted()).toEqual(["headline", "id", "query"]);
    expect(blindPool({ s: { q: [h("a"), h("b")] } }, 7)).toEqual(blindPool({ s: { q: [h("a"), h("b")] } }, 7));
  });

  it("pools only the first ten distinct stories", () => {
    const rows = [h("dup"), h("dup"), ...Array.from({ length: 12 }, (_, i) => h(`h${i}`))];
    expect(blindPool({ s: { q: rows } }, 1)).toHaveLength(10);
  });
});

describe("permuteLabels", () => {
  it("keeps each query's base rate and moves values between its headlines", () => {
    const l = labels("q", { a: true, b: false, c: false, d: false });
    const p = permuteLabels(l, 3);
    expect([...p.values()].filter(Boolean)).toHaveLength(1);
    expect([...p.keys()].toSorted()).toEqual([...l.keys()].toSorted());
  });
});
