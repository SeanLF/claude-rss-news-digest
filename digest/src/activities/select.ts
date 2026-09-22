import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ApplicationFailure } from "@temporalio/common";
import { parse } from "csv-parse/sync";
import { z } from "zod";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";

export const SELECT_OUTPUT = "selected.json";
const REQUIRED = ["clusters.json", "recap.txt", "sources.csv"];
const OPTIONAL = ["weekly_recap.txt", "yesterday_headlines.txt"];

const Pick = z.object({ cluster_index: z.number().int(), article_ids: z.array(z.string()) });
// Shape only, never count: the tier targets live in the prompt, not the grammar.
export const SelectedSchema = z.object({ must_know: z.array(Pick), should_know: z.array(Pick), not_covered_blurb: z.string().optional() });
export type Selected = z.infer<typeof SelectedSchema>;
export const selectedJsonSchema = (): Record<string, unknown> => z.toJSONSchema(SelectedSchema, { target: "draft-07" });

// The content checks the Python never made: every cited id exists in the run and every pick cites
// at least one. A violation is a content problem, so the activity fails retryably and the next
// attempt is a fresh sample (spec §2.2, failure tier 2), never a patched one.
export function checkSelected(s: Selected, knownIds: ReadonlySet<string>): string[] {
  const problems: string[] = [];
  for (const tier of ["must_know", "should_know"] as const)
    s[tier].forEach((p, i) => {
      if (p.article_ids.length === 0) problems.push(`${tier}[${i}] cites no articles`);
      const unknown = p.article_ids.filter((a) => !knownIds.has(a));
      if (unknown.length) problems.push(`${tier}[${i}] cites unknown ids ${unknown.slice(0, 5).join(",")}`);
    });
  if (s.must_know.length + s.should_know.length === 0) problems.push("selected nothing");
  return problems;
}

const articleIds = (csv: string): string[] =>
  parse<Record<string, string>>(csv, { columns: true, skip_empty_lines: true, relax_column_count: true }).flatMap((r) => (r["article_id"] ? [r["article_id"]] : []));

export interface SelectDeps {
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: { stage: string; runId: number; costUsd: number; durationMs: number; numTurns: number; toolCalls: number }) => void;
}

// SELECT keeps its read loop: ~250 KB of inputs, read selectively. The inputs are materialised
// into a scratch directory, links scrubbed, as the model's only Read surface, and discarded after.
export function selectActivity(deps: SelectDeps) {
  return async (runId: number, _clusters: Pointer, _recap: Pointer, note?: string, input?: { force?: boolean }): Promise<Pointer> => {
    const { store } = deps;
    const force = input?.force ?? false;
    const existing = store.find(runId, SELECT_OUTPUT);
    if (existing && !force) {
      const parsed = SelectedSchema.safeParse(JSON.parse(store.get(existing)));
      if (parsed.success) return existing;
      store.quarantine(runId, SELECT_OUTPUT);
    }
    const names = store.names(runId);
    const csvs = names.filter((n) => /^articles_\d+\.csv$/.test(n));
    for (const r of REQUIRED) if (!names.includes(r)) throw ApplicationFailure.nonRetryable(`run ${runId} has no ${r}`, "MissingInput");
    const dir = mkdtempSync(join(tmpdir(), `select-${runId}-`));
    const known = new Set<string>();
    try {
      for (const name of [...REQUIRED, ...OPTIONAL, ...csvs]) {
        const p = store.find(runId, name);
        if (!p) continue;
        const text = scrubUrls(store.get(p));
        assertNoUrls(text);
        writeFileSync(join(dir, name), text);
        if (csvs.includes(name)) for (const id of articleIds(text)) known.add(id);
      }
      const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "select.md"), "utf8"));
      const message = `The input directory is ${dir}. Begin.${note ? `\n\nOperator note for this attempt: ${note}` : ""}`;
      deps.heartbeat?.();
      const r = await runStage(spec, { userMessage: message, inputDir: dir }, { today: store.runDate(runId), outputSchema: selectedJsonSchema(), ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}) });
      deps.heartbeat?.();
      deps.onUsage?.({ stage: "select", runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, toolCalls: r.toolCalls.length });
      const parsed = SelectedSchema.safeParse(r.structured);
      if (!parsed.success) throw new Error(`select for run ${runId}: output does not match the schema`);
      const problems = checkSelected(parsed.data, known);
      if (problems.length) throw new Error(`select for run ${runId}: ${problems.join("; ")}`);
      const text = JSON.stringify(parsed.data, null, 2);
      return force ? store.replace(runId, SELECT_OUTPUT, text) : store.put(runId, SELECT_OUTPUT, text);
    } finally {
      rmSync(dir, { recursive: true, force: true }); // a mkdtemp directory this call created
    }
  };
}
