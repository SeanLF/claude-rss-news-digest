import type { DatabaseSync } from "node:sqlite";

// One model call's usage, recorded in the production run_usage table (the spec's cost source) so a
// run's cost is read from the database, not scraped from logs.
export interface UsageRow {
  stage: string;
  runId: number;
  model: string;
  thinking: string;
  effort: string;
  costUsd: number;
  durationMs: number;
  tokens: Record<string, number>;
  [detail: string]: unknown;
}

export function recordUsage(db: DatabaseSync, row: UsageRow): void {
  const t = row.tokens;
  db.prepare(
    "INSERT INTO run_usage (run_id, subagent, model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, api_cost_usd, duration_ms, thinking, effort) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(row.runId, row.stage, row.model, t["input_tokens"] ?? 0, t["output_tokens"] ?? 0, t["cache_creation_input_tokens"] ?? 0, t["cache_read_input_tokens"] ?? 0, row.costUsd, row.durationMs, row.thinking, row.effort);
}

export function runCost(db: DatabaseSync, runId: number, since: string): { costUsd: number; calls: number } {
  const r = db.prepare("SELECT COALESCE(SUM(api_cost_usd), 0) AS c, COUNT(*) AS n FROM run_usage WHERE run_id=? AND recorded_at >= ?").get(runId, since) as { c: number; n: number };
  return { costUsd: r.c, calls: r.n };
}
