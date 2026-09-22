import type { UsageRow } from "../store/usage.js";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { assertNoUrls } from "../contracts/ids.js";
import { PREHEADER_MAX_CHARS } from "../contracts/selections.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import type { StoryPlan } from "./index.js";
import type { DraftStory } from "./write.js";

// {input, line}: the headlines it was written from, so a resume with rewritten drafts writes a new one.
export const PREHEADER_OUTPUT = "preheader.json";
export const preheaderLine = (text: string): string => (JSON.parse(text) as { line: string }).line;

// Truncate to <= max chars ending in an ellipsis, on a word boundary (merge._truncate_on_word_boundary).
// Counted in code points, as Python's len() and slicing do, so an emoji at the cut is never split.
export function truncateOnWordBoundary(input: string, max: number): string {
  const text = Array.from(input);
  if (text.length <= max) return input;
  const budget = max - 1;
  let head = text.slice(0, budget).join("");
  if (!/\s/.test(text[budget] ?? "") && !/\s/.test(text[budget - 1] ?? "")) {
    const cut = head.lastIndexOf(" ");
    if (cut > 0) head = head.slice(0, cut);
  }
  return `${head.trimEnd()}…`;
}

// A closed set of label prefixes, not a shape: `^word+:` would decapitate "WHO: companies filed..."
// (orchestrate.clean_preheader, measured against 1,225 archived headlines).
const LABELS = ["preheader", "preheader line", "here is the preheader", "the preheader"];
const LABEL_RE = new RegExp(`^\\**\\s*(?:${LABELS.map((l) => l.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\s*\\**\\s*:\\**\\s*`, "i");
const LIST_RE = /^\s*(?:[-*•]|\d+[.)])\s+/;
const QUOTES: [string, string][] = [['"', '"'], ["'", "'"], ["“", "”"], ["‘", "’"]];

// The first usable line, unlabelled, unlisted, unquoted, within the cap. "" when nothing usable is
// left, which assemble fills from the first headline: this field degrades, it never aborts a digest.
export function cleanPreheader(text: string): string {
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("```"));
  while (lines.length && lines[0]!.replace(LABEL_RE, "").trim() === "") lines.shift();
  if (!lines.length) return "";
  let first = lines[0]!.replace(LIST_RE, "").replace(LABEL_RE, "").trim();
  for (const [o, c] of QUOTES) if (first.length > 1 && first.startsWith(o) && first.endsWith(c)) first = first.slice(1, -1).trim();
  return truncateOnWordBoundary(first, PREHEADER_MAX_CHARS);
}

export interface PreheaderDeps {
  signal?: () => AbortSignal | undefined;
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: UsageRow) => void;
}

// Best-effort by design: a failed call or an unusable reply stores "" and assemble substitutes the
// top headline. Only a successful non-empty write is kept for idempotency.
export function preheaderActivity(deps: PreheaderDeps) {
  return async (runId: number, drafts: Pointer[], force = false): Promise<Pointer> => {
    const { store } = deps;
    const heads: Record<"must_know" | "should_know", { headline: string }[]> = { must_know: [], should_know: [] };
    const items = drafts.map((d) => JSON.parse(store.get(d)) as { plan: StoryPlan; story: DraftStory }).toSorted((a, b) => a.plan.index - b.plan.index);
    for (const { plan, story } of items) heads[plan.tier].push({ headline: story.headline });
    const input = JSON.stringify(heads);
    const existing = store.find(runId, PREHEADER_OUTPUT);
    if (existing && !force) {
      const prior = JSON.parse(store.get(existing)) as { input?: string; line?: string };
      if (prior.input === input && prior.line?.trim()) return existing;
      store.quarantine(runId, PREHEADER_OUTPUT);
    }
    const message = JSON.stringify(heads, null, 2);
    assertNoUrls(message);
    const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "preheader.md"), "utf8"));
    deps.heartbeat?.();
    const r = await runStage(spec, { userMessage: message, inputDir: tmpdir() }, { today: store.runDate(runId), ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}) });
    deps.onUsage?.({ model: spec.model, thinking: spec.thinking, tokens: r.usage, stage: "preheader", runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns });
    const line = cleanPreheader(r.text);
    if (!line) throw new Error(`preheader for run ${runId}: nothing usable in the reply`);
    const doc = JSON.stringify({ input, line });
    return force ? store.replace(runId, PREHEADER_OUTPUT, doc) : store.put(runId, PREHEADER_OUTPUT, doc);
  };
}
