import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Context } from "@temporalio/activity";
import { assertNoUrls } from "../contracts/ids.js";
import { previousHeadlines, runAt } from "../prepare/context.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import type { UsageRow } from "../store/usage.js";

export const WEEKLY_RECAP = "weekly_recap.txt";
export const WEEKLY_RECAP_MAX_WEEKS = 6;
const DAY_MS = 86_400_000;

export interface WeeklyRecapDeps {
  store: ArtifactStore;
  dbPath: string;
  agentsDir: string;
  // The attempts the workflow's policy allows; only the last one gives up and carries the old recap.
  maxAttempts: number;
  attempt?: () => number;
  query?: SdkQuery;
  heartbeat?: () => void;
  signal?: () => AbortSignal | undefined;
  onUsage?: (row: UsageRow) => void;
}

const currentAttempt = (): number => {
  try {
    return Context.current().info.attempt;
  } catch {
    return Number.POSITIVE_INFINITY; // outside an activity every attempt is the last
  }
};

// run.py's maybe_update_weekly_recap. The rolling file lives in the archive rather than in data/: each
// run's copy is the latest earlier run's, with a new week appended (last six kept) once the newest
// entry is a week old. Stored before SELECT, so the run that writes a week also reads it.
export function weeklyRecapActivity(deps: WeeklyRecapDeps): (runId: number, force?: boolean) => Promise<Pointer | null> {
  return async (runId, force = false) => {
    const { store } = deps;
    const existing = store.find(runId, WEEKLY_RECAP);
    if (existing && !force) return existing;
    const write = (text: string): Pointer => (force ? store.replace(runId, WEEKLY_RECAP, text) : store.put(runId, WEEKLY_RECAP, text));
    const db = openDb(deps.dbPath);
    let prior: string | null;
    let titles: string;
    try {
      prior = (db.prepare("SELECT content FROM run_artifacts WHERE artifact_name=? AND run_id < ? ORDER BY run_id DESC LIMIT 1").get(WEEKLY_RECAP, runId) as { content: string } | undefined)?.content ?? null;
      titles = previousHeadlines(db, runAt(db, runId)).map((t) => t.headline).filter(Boolean).map((t) => `- ${t}`).join("\n");
    } finally {
      db.close();
    }
    const carry = (): Pointer | null => (prior === null ? null : write(prior));
    const today = store.runDate(runId);
    const last = prior === null ? undefined : [...prior.matchAll(/^## Week of (\d{4}-\d{2}-\d{2})/gm)].at(-1)?.[1];
    const age = last ? (Date.parse(`${today}T00:00:00Z`) - Date.parse(`${last}T00:00:00Z`)) / DAY_MS : Number.NaN;
    if (age < 7) return carry(); // NaN (no entry, or an unreadable date) regenerates
    if (!titles) return carry();
    assertNoUrls(titles);
    let summary: string;
    try {
      const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "weekly-recap.md"), "utf8"));
      deps.heartbeat?.();
      const r = await runStage(spec, { userMessage: titles, inputDir: tmpdir() }, { today, ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}) });
      deps.onUsage?.({ model: spec.model, thinking: spec.thinking, effort: r.effort, tokens: r.usage, stage: "weekly_recap", runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns });
      summary = r.text.trim();
      if (!summary) throw new Error("model returned an empty recap");
    } catch (e) {
      if (deps.signal?.()?.aborted) throw e;
      if ((deps.attempt ?? currentAttempt)() < deps.maxAttempts) throw e;
      // Best-effort, as in the Python: the run goes on with last week's recap.
      console.warn(`Weekly recap generation failed (non-fatal): ${String(e)}`);
      return carry();
    }
    const sections = (prior ?? "").split(/(?=^## Week of )/m).filter((s) => s.trim());
    sections.push(`## Week of ${today}\n${summary}\n\n`);
    return write(sections.slice(-WEEKLY_RECAP_MAX_WEEKS).join(""));
  };
}
