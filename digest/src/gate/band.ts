import type { JudgeVerdict } from "./verdict.js";

const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

// A judge's self-agreement across repeated runs on the same digest (spec §7 item 2): the share of
// (story, criterion) cells on which every run gave the same verdict, overall and per criterion.
// A criterion under 0.8 is not usable as a gate criterion until the rubric is tightened.
export function selfAgreement(runs: JudgeVerdict[][]): { perCriterion: Record<number, number>; overall: number } {
  const cells = new Map<string, boolean[]>();
  for (const run of runs)
    for (const v of run) {
      const k = `${v.story}:${v.criterion}`;
      cells.set(k, [...(cells.get(k) ?? []), v.pass]);
    }
  const byCriterion: Record<number, number[]> = {};
  let agree = 0;
  for (const [k, votes] of cells) {
    const same = votes.every((x) => x === votes[0]) ? 1 : 0;
    agree += same;
    const c = Number(k.split(":")[1]);
    (byCriterion[c] ??= []).push(same);
  }
  return { perCriterion: Object.fromEntries(Object.entries(byCriterion).map(([c, xs]) => [Number(c), avg(xs)])), overall: cells.size ? agree / cells.size : 0 };
}
