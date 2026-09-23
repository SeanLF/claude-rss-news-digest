// usage: run-health <first-run> [last-run] [--broadcasting]
// Prints one JSON line per run: its post-run violations and COHERENCE failure kinds, read-only over
// DIGEST_DB_PATH. THREADS_ENABLED is read from the environment, as the worker reads it.
import { DatabaseSync } from "node:sqlite";
import { dbPath } from "../store/db.js";
import { coherenceKindCounts, getRunHealth, threadsEnabled, violations } from "../ops/run-health.js";

const [first, last] = process.argv.slice(2).filter((a) => !a.startsWith("--")).map(Number);
if (first === undefined || Number.isNaN(first)) throw new Error("usage: run-health <first-run> [last-run] [--broadcasting]");
const db = new DatabaseSync(dbPath(), { readOnly: true });
const report = db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='coherence_report.json'");
for (let run = first; run <= (last ?? first); run++) {
  const health = getRunHealth(db, run, { broadcasting: process.argv.includes("--broadcasting"), threadsEnabled: threadsEnabled(), usageRowsDropped: 0 });
  const text = (report.get(run) as { content: string } | undefined)?.content ?? null;
  console.log(JSON.stringify({ run, violations: violations(health), kinds: coherenceKindCounts(text) }));
}
db.close();
