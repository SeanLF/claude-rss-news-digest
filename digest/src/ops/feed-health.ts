import type { DatabaseSync } from "node:sqlite";
import type { AlertRequest } from "./alerts.js";

// db.get_failing_sources and run.py's source-health alert: sources seen in the last seven days whose
// most recent fetches (up to ten) failed at least `threshold` times in a row. Only the run's fetch set
// counts: a parked source's failures are history we chose to stop making.
export function feedHealthAlert(db: DatabaseSync, runId: number, fetchSet: readonly string[], threshold: number): Extract<AlertRequest, { kind: "source-health" }> | null {
  const recent = db.prepare("SELECT DISTINCT source_id AS id FROM source_health WHERE recorded_at > datetime('now', '-7 days')").all() as { id: string }[];
  const latest = db.prepare("SELECT success FROM source_health WHERE source_id = ? ORDER BY recorded_at DESC LIMIT 10");
  const fetched = new Set(fetchSet);
  const failing: [string, number][] = [];
  for (const { id } of recent) {
    if (!fetched.has(id)) continue;
    const rows = latest.all(id) as { success: number }[];
    const streak = rows.findIndex((r) => r.success);
    const count = streak === -1 ? rows.length : streak;
    if (count >= threshold) failing.push([id, count]);
  }
  if (!failing.length) return null;
  failing.sort((a, b) => b[1] - a[1]);
  const { n } = db.prepare("SELECT COUNT(*) AS n FROM source_health WHERE run_id = ? AND success = 0").get(runId) as { n: number };
  return { kind: "source-health", failing, failedThisRun: n, totalSources: fetchSet.length, threshold };
}
