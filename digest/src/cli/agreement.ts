// usage: agreement <promptfoo results.json>   the one number promptfoo does not compute: each judge's
// self-agreement across its repeats, and the cells where the two judges' first repeats disagree.
import { readFileSync } from "node:fs";
import { selfAgreement } from "../gate/band.js";
import { disagreements } from "../gate/score.js";
import { parseVerdicts, type JudgeVerdict } from "../gate/verdict.js";

const file = process.argv[2];
if (!file) throw new Error("usage: agreement <promptfoo results.json>");
const doc = JSON.parse(readFileSync(file, "utf8")) as { results: { results: { provider: { id: string; label?: string }; response?: { output?: string } }[] } };
// Loud rather than a report of zero reps if promptfoo's results shape ever changes.
if (!Array.isArray(doc.results?.results)) throw new Error(`${file}: not a promptfoo results file (no results.results)`);
const runs = new Map<string, JudgeVerdict[][]>();
for (const r of doc.results.results) {
  const name = r.provider.label ?? r.provider.id;
  const out = r.response?.output;
  if (!out) continue;
  runs.set(name, [...(runs.get(name) ?? []), parseVerdicts(out)]);
}
if (runs.size < 2) throw new Error(`${file}: expected two judges with outputs, found ${runs.size}`);
const [a, b] = [...runs.keys()];
const report = {
  judges: Object.fromEntries([...runs].map(([n, rs]) => [n, { reps: rs.length, ...selfAgreement(rs) }])),
  disagreements: a && b ? disagreements(runs.get(a)![0]!, runs.get(b)![0]!) : [],
};
console.log(JSON.stringify(report, null, 2));
