import type { Sql } from "../store/db.js";
import type { AlertRequest } from "./alerts.js";

// db.get_failing_sources and run.py's source-health alert: sources seen in the last seven days whose
// most recent fetches (up to ten) failed at least `threshold` times in a row. Only the run's fetch set
// counts: a parked source's failures are history we chose to stop making.
export async function feedHealthAlert(db: Sql, runId: number, fetchSet: readonly string[], threshold: number): Promise<Extract<AlertRequest, { kind: "source-health" }> | null> {
  const recent = await db.all<{ id: string }>("SELECT DISTINCT source_id AS id FROM source_fetches WHERE fetched_at > now() - interval '7 days'");
  const fetched = new Set(fetchSet);
  const failing: [string, number][] = [];
  for (const { id } of recent) {
    if (!fetched.has(id)) continue;
    const rows = await db.all<{ is_success: boolean }>("SELECT is_success FROM source_fetches WHERE source_id = $1 ORDER BY fetched_at DESC, id DESC LIMIT 10", [id]);
    const streak = rows.findIndex((r) => r.is_success);
    const count = streak === -1 ? rows.length : streak;
    if (count >= threshold) failing.push([id, count]);
  }
  if (!failing.length) return null;
  failing.sort((a, b) => b[1] - a[1]);
  const r = await db.one<{ n: number }>("SELECT COUNT(*) AS n FROM source_fetches WHERE run_id = $1 AND NOT is_success", [runId]);
  return { kind: "source-health", failing, failedThisRun: r!.n, totalSources: fetchSet.length, threshold };
}
