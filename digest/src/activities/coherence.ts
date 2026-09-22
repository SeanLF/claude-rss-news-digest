import type { UsageRow } from "../store/usage.js";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CoherenceReportSchema, coherenceReportJsonSchema, type CoherenceReport } from "../contracts/coherence.js";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { itemIds, normHeadline, resultMatches } from "../contracts/match.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import type { StoryPlan } from "./index.js";
import type { DraftStory } from "./write.js";

export const COHERENCE_OUTPUT = "coherence_report.json";
export const DRAFT_OUTPUT = "draft_selections.json";

// orchestrate.build_inline_grep_body, ported: only steps 1 and 3 change (how the input arrives, how
// the result leaves). Step 2 -- verify ONLY against the story's cited ids -- is untouched, and a
// drifted prompt throws rather than quietly checking against the wrong rule.
const STEP_1 = "1. Use the Read tool to read these files:";
const STEP_2 = "2. For each story in draft_selections.json";
const WRITE_STEP = /3\. Use the Write tool to write the result to `[^`]*coherence_report\.json`\n/;
const TOOLS_RULE = "- DO NOT use Bash. Use Read and Write tools only.\n";
const REPLY_JSON = "**Reply with the JSON object and nothing else** -- no preamble, no code fence, no commentary.\n";
export function buildInlineGrepBody(body: string, inputDir: string): string {
  if (!body.includes(STEP_1) || !body.includes(STEP_2) || !WRITE_STEP.test(body) || !body.includes(TOOLS_RULE))
    throw new Error("coherence body drifted: an instruction step is not where buildInlineGrepBody expects");
  const step1 = `1. **Your input arrives in the next message, inline**: draft_selections.json, every articles_*.csv, and article_fulltext.json. The same files are also on disk under \`${inputDir}/\`.\n`;
  let out = body.slice(0, body.indexOf(STEP_1)) + step1 + body.slice(body.indexOf(STEP_2));
  const step3 = "3. **Rule: before you FAIL a field, use the Grep tool (or Read) on the on-disk files to confirm the specific is absent from, or contradicted by, the story's cited sources.** A FAIL with no tool call behind it is not allowed; a PASS needs none.\n   " + REPLY_JSON;
  out = out.replace(WRITE_STEP, step3).replace(TOOLS_RULE, "- DO NOT use Bash or Write. Use Grep and Read only.\n");
  return out.replace("before writing that story's result", "before giving that story's result");
}

// Fails whose reason quotes no Grep pattern of the attempt (eval_coherence.unbacked_fail_count):
// approximate attribution, reported with the usage, never gated.
export function unbackedFails(report: CoherenceReport, toolCalls: { name: string; target: string }[]): number {
  const pats = toolCalls.filter((t) => t.name === "Grep" && t.target.trim().length >= 4).map((t) => t.target.trim().toLowerCase());
  return report.results.filter((r) => !r.pass && !pats.some((p) => r.reason.toLowerCase().includes(p))).length;
}

export type Draft = { must_know: DraftStory[]; should_know: DraftStory[]; preheader: string };

// Fan-in in SELECT's order onto SELECT's tier (write_fanout.assemble_draft).
export function draftFrom(store: ArtifactStore, drafts: Pointer[]): Draft {
  const out: Draft = { must_know: [], should_know: [], preheader: "" };
  const items = drafts.map((d) => JSON.parse(store.get(d)) as { plan: StoryPlan; story: DraftStory }).toSorted((a, b) => a.plan.index - b.plan.index);
  for (const { plan, story } of items) out[plan.tier].push(story);
  return out;
}

// Every draft story must have a matching result: an unchecked story is a stage failure, not a
// soft signal (orchestrate.validate_coherence). Identity-based, never count-based.
export function uncovered(report: CoherenceReport, draft: Draft): string[] {
  return [...draft.must_know, ...draft.should_know]
    .filter((s) => !report.results.some((r) => resultMatches(r, itemIds(s.sources), normHeadline(s.headline))))
    .map((s) => s.headline);
}

export interface CoherenceDeps {
  signal?: () => AbortSignal | undefined;
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: UsageRow) => void;
}

// The checker is a verdict: one attempt (spec §2.1), so a failure here parks or fails the run
// rather than re-sampling until something passes.
export function coherenceActivity(deps: CoherenceDeps) {
  return async (runId: number, drafts: Pointer[], _fulltext: Pointer, note?: string, force = false): Promise<Pointer> => {
    const { store } = deps;
    const draft = draftFrom(store, drafts);
    const draftText = JSON.stringify(draft, null, 2);
    const existingDraft = store.find(runId, DRAFT_OUTPUT);
    const sameDraft = existingDraft !== undefined && store.get(existingDraft) === draftText;
    const existing = store.find(runId, COHERENCE_OUTPUT);
    if (existing && sameDraft && !force) {
      const parsed = CoherenceReportSchema.safeParse(JSON.parse(store.get(existing)));
      if (parsed.success && uncovered(parsed.data, draft).length === 0) return existing;
    }
    if (!force) {
      if (existing) store.quarantine(runId, COHERENCE_OUTPUT);
      if (existingDraft && !sameDraft) store.quarantine(runId, DRAFT_OUTPUT); // a matching draft is kept
    }
    const checked = await runChecker(deps, runId, draftText, note);
    const { report: parsedReport, costUsd, durationMs, numTurns, toolCalls, unbacked } = checked;
      deps.onUsage?.({ model: checked.model, thinking: checked.thinking, tokens: checked.tokens, stage: "coherence", runId, costUsd, durationMs, numTurns, toolCalls, unbackedFails: unbacked });
      const parsed = { data: parsedReport };
    const gaps = uncovered(parsed.data, draft);
    if (gaps.length) throw new Error(`coherence for run ${runId}: no result matches ${gaps.length} draft story(ies): ${gaps.slice(0, 3).join("; ")}`);
    const write = (name: string, text: string) => (force ? store.replace(runId, name, text) : store.put(runId, name, text));
    if (force || !sameDraft) write(DRAFT_OUTPUT, draftText);
    return write(COHERENCE_OUTPUT, JSON.stringify(parsed.data, null, 2));
  };
}

// One checker run over a draft: the draft, the article CSVs and the fulltext inline and on disk,
// links scrubbed, the inline+Grep body derived from the shipped prompt. The scoped recheck after a
// repair is this same run over a draft holding only the patched stories.
export async function runChecker(deps: CoherenceDeps, runId: number, draftText: string, note?: string) {
  const { store } = deps;
  const dir = mkdtempSync(join(tmpdir(), `coherence-${runId}-`));
  try {
    const files: [string, string][] = [[DRAFT_OUTPUT, draftText]];
    for (const name of store.names(runId).filter((n) => /^articles_\d+\.csv$/.test(n) || n === "article_fulltext.json").toSorted()) files.push([name, store.get(store.find(runId, name)!)]);
    const parts: string[] = [];
    for (const [name, raw] of files) {
      const text = scrubUrls(raw);
      assertNoUrls(text);
      writeFileSync(join(dir, name), text);
      parts.push(`## ${name}\n\n${text}`);
    }
    const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "coherence.md"), "utf8"));
    const body = buildInlineGrepBody(spec.body, dir);
    deps.heartbeat?.();
    const r = await runStage({ ...spec, body }, { userMessage: parts.join("\n\n") + (note ? `\n\nOperator note for this attempt: ${note}` : ""), inputDir: dir }, {
      today: store.runDate(runId),
      outputSchema: coherenceReportJsonSchema(),
      ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}),
    });
    deps.heartbeat?.();
    const parsed = CoherenceReportSchema.safeParse(r.structured);
    if (!parsed.success) throw new Error(`coherence for run ${runId}: report does not match the schema`);
    return { model: spec.model, thinking: spec.thinking, tokens: r.usage, report: parsed.data, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, toolCalls: r.toolCalls.length, unbacked: unbackedFails(parsed.data, r.toolCalls) };
  } finally {
    rmSync(dir, { recursive: true, force: true }); // the mkdtemp directory this call created
  }
}
