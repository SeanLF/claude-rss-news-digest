// A promptfoo JavaScript assertion: score one checker report against the planted key. Passes at the
// old checker's band on the same fixture (spec §2.2's threshold: not worse on recall, not worse on
// false drops): every plant caught and at most maxFalseDrops clean fields dropped.
import { readFileSync } from "node:fs";
import { CoherenceReportSchema } from "../contracts/coherence.js";
import { scorePlanted, type PlantedKey } from "./planted-score.js";

export default function plantedAssert(output: string, context: { vars: { draft: string; key: string; maxFalseDrops: number } }) {
  const report = CoherenceReportSchema.parse(JSON.parse(output));
  const draft = JSON.parse(readFileSync(context.vars.draft, "utf8")) as Parameters<typeof scorePlanted>[1];
  const key = JSON.parse(readFileSync(context.vars.key, "utf8")) as PlantedKey;
  const s = scorePlanted(report, draft, key);
  const pass = s.recall === s.planted && s.falseDrops <= context.vars.maxFalseDrops;
  return {
    pass,
    score: s.recall / s.planted,
    reason: `recall ${s.recall}/${s.planted}, false drops ${s.falseDrops}/${s.clean}; missed [${s.missed.join(", ")}] dropped [${s.dropped.join(", ")}]`,
    namedScores: { recall: s.recall, falseDrops: s.falseDrops },
  };
}
