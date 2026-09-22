// usage: run-stage --agent <path.md> --input-dir <dir> --today YYYY-MM-DD [--schema coherence] [--inline-corpus]
// The stage primitive the evals call: one agent file over one input directory, StageResult as JSON
// on stdout. Never touches Temporal.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { coherenceReportJsonSchema } from "../contracts/coherence.js";
import { assertNoUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage } from "../runner/run-stage.js";

export interface Invocation {
  agentPath: string;
  inputDir: string;
  today: string;
  schema?: "coherence";
  inlineCorpus: boolean;
}

export function buildInvocation(argv: string[]): Invocation {
  const get = (flag: string): string | undefined => {
    const i = argv.indexOf(flag);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  const agentPath = get("--agent");
  const inputDir = get("--input-dir");
  const today = get("--today");
  if (!agentPath) throw new Error("--agent is required");
  if (!inputDir) throw new Error("--input-dir is required");
  if (!today) throw new Error("--today is required");
  const schema = get("--schema");
  if (schema !== undefined && schema !== "coherence") throw new Error(`unknown schema ${schema}`);
  return { agentPath, inputDir, today, ...(schema ? { schema } : {}), inlineCorpus: argv.includes("--inline-corpus") };
}

// The checker's corpus inline in the user turn (spec §2.2's favoured shape): the draft, the
// article CSVs and the fulltext, in name order, and nothing else.
export function inlineCorpus(inputDir: string): string {
  const names = readdirSync(inputDir)
    .filter((n) => n === "draft_selections.json" || /^articles_\d+\.csv$/.test(n) || n === "article_fulltext.json")
    .toSorted();
  const corpus = names.map((n) => `## ${n}\n\n${readFileSync(join(inputDir, n), "utf8")}`).join("\n\n");
  assertNoUrls(corpus); // the no-URL invariant, checked where the text leaves code (spec §1)
  return corpus;
}

if (process.argv[1]?.endsWith("run-stage.js")) {
  const inv = buildInvocation(process.argv.slice(2));
  const spec = parseAgentSpec(readFileSync(inv.agentPath, "utf8"));
  const result = await runStage(
    spec,
    { userMessage: inv.inlineCorpus ? inlineCorpus(inv.inputDir) : "Begin.", inputDir: inv.inputDir },
    { today: inv.today, ...(inv.schema === "coherence" ? { outputSchema: coherenceReportJsonSchema() } : {}) },
  );
  process.stdout.write(JSON.stringify(result));
}
