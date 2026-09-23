// usage: run-health <first-run> [last-run] [--broadcasting]
// Prints one JSON line per run: its post-run violations and COHERENCE failure kinds, read-only over
// DIGEST_DATABASE_URL. THREADS_ENABLED is read from the environment, as the worker reads it.
import { threadsConfigFrom } from "../activities/threads.js";
import { dbUrl, openDb } from "../store/db.js";
import { coherenceKindCounts, getRunHealth, threadsEnabled, violations } from "../ops/run-health.js";

const [first, last] = process.argv.slice(2).filter((a) => !a.startsWith("--")).map(Number);
if (first === undefined || Number.isNaN(first)) throw new Error("usage: run-health <first-run> [last-run] [--broadcasting]");
const db = openDb(dbUrl());
for (let run = first; run <= (last ?? first); run++) {
  const health = await getRunHealth(db, run, { broadcasting: process.argv.includes("--broadcasting"), threadsEnabled: threadsEnabled(), usageRowsDropped: 0, dormantAfter: threadsConfigFrom(process.env).dormantAfter });
  const text = (await db.one<{ content: string }>("SELECT content FROM artifacts WHERE run_id=$1 AND name='coherence_report.json' AND status='current'", [run]))?.content ?? null;
  console.log(JSON.stringify({ run, violations: violations(health), kinds: coherenceKindCounts(text) }));
}
process.exit(0); // the pool would otherwise hold the process open
