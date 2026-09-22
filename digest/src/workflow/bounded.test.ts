import { describe, expect, it } from "vitest";
import { mapBounded } from "./bounded.js";

describe("mapBounded", () => {
  it("never exceeds the limit, keeps input order, and settles failures without stopping the rest", async () => {
    let inFlight = 0;
    let peak = 0;
    const out = await mapBounded([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 5));
      inFlight--;
      if (n === 4) throw new Error("four");
      return n * 10;
    });
    expect(peak).toBe(3);
    expect(out.map((r) => (r.status === "fulfilled" ? r.value : "x"))).toEqual([10, 20, 30, "x", 50, 60, 70]);
  });
  it("handles an empty list", async () => {
    expect(await mapBounded([], 4, () => Promise.resolve(1))).toEqual([]);
  });
});
