// usage: gate --digest digest.html --inputs <dir> --judges gate/judges.json [--reps 5] [--out band.json]
// Runs every judge `reps` times on one digest, writes each judge's self-agreement band and runs,
// and the first-run disagreements between the first two judges (spec §7 item 2). Two families
// are required.
import { readFileSync, writeFileSync } from "node:fs";
import { selfAgreement } from "../gate/band.js";
import { cliJudge, type Judge } from "../gate/judge.js";
import { disagreements } from "../gate/score.js";
import type { JudgeVerdict } from "../gate/verdict.js";

const get = (f: string): string => {
  const i = process.argv.indexOf(f);
  const v = i >= 0 ? process.argv[i + 1] : undefined;
  if (!v) throw new Error(`${f} is required`);
  return v;
};
const digest = readFileSync(get("--digest"), "utf8");
const inputs = get("--inputs");
const reps = Number(process.argv.includes("--reps") ? get("--reps") : 5);
const judges: Judge[] = (JSON.parse(readFileSync(get("--judges"), "utf8")) as { name: string; family: Judge["family"]; command: string[] }[]).map((j) =>
  cliJudge(j.name, j.family, j.command),
);
if (new Set(judges.map((j) => j.family)).size < 2) throw new Error("the gate needs judges from two families (spec §7.2)");
const out: Record<string, unknown> = {};
const first: Record<string, JudgeVerdict[]> = {};
for (const j of judges) {
  const runs: JudgeVerdict[][] = [];
  for (let i = 0; i < reps; i++) {
    runs.push(await j.run(digest, inputs));
    console.error(`${j.name}: rep ${i + 1}/${reps} done, ${runs[i]!.length} verdicts`);
  }
  first[j.name] = runs[0]!;
  out[j.name] = { band: selfAgreement(runs), runs };
}
const [a, b] = judges;
const dis = a && b ? disagreements(first[a.name]!, first[b.name]!) : [];
out["disagreements"] = dis;
writeFileSync(process.argv.includes("--out") ? get("--out") : "band.json", JSON.stringify(out, null, 2));
console.log(JSON.stringify({ judges: judges.map((j) => j.name), disagreements: dis.length }));
