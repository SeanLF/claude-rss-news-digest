import type { CoherenceReport } from "../contracts/coherence.js";
import type { Plant } from "./plant.js";
import type { Criterion, JudgeVerdict } from "./verdict.js";

// Recall of the checker on planted defects: a plant is caught when its story's field is flagged.
// Scored by story index in draft order; the runner's structured output keeps order.
export function recallOnPlants(plants: Plant[], report: CoherenceReport): { caught: number; total: number } {
  let caught = 0;
  for (const p of plants) {
    const r = report.results[p.storyIndex];
    if (r && !r.pass && (r.failed_fields ?? []).includes(p.field)) caught++;
  }
  return { caught, total: plants.length };
}

// The cells where two judges differ: what goes to Sean, the only human label in the loop.
export function disagreements(a: JudgeVerdict[], b: JudgeVerdict[]): { story: number; criterion: Criterion }[] {
  const bs = new Map(b.map((v) => [`${v.story}:${v.criterion}`, v.pass]));
  return a
    .filter((v) => {
      const k = `${v.story}:${v.criterion}`;
      return bs.has(k) && bs.get(k) !== v.pass;
    })
    .map((v) => ({ story: v.story, criterion: v.criterion }));
}
