// usage: threads-live RUN [--link-only]
// The threads phase for one run, live, outside Temporal: link, synthesize each continuing thread in
// turn (unless --link-only), finish, against DIGEST_DB_PATH, with every model call's usage recorded in run_usage. Point it
// at a scratch copy (bin/threads-oracle's pre.db is the run's state just before it). Prints what the
// linker decided beside what the archive recorded, and what the phase cost.
import { THREAD_ASSIGNMENTS, THREAD_LINKS, threadsActivities, threadsConfigFrom } from "../activities/threads.js";
import { DEFAULT_AGENTS_DIR } from "../activities/real.js";
import { ArtifactStore } from "../store/artifacts.js";
import { dbPath, openDb } from "../store/db.js";
import { recordUsage, runCost } from "../store/usage.js";

const run = Number(process.argv[2]);
if (!Number.isInteger(run)) throw new Error("usage: threads-live RUN");
const store = new ArtifactStore(dbPath());
const db = openDb(dbPath());
const since = (db.prepare("SELECT datetime('now') AS t").get() as { t: string }).t;
const acts = threadsActivities({ store, dbPath: dbPath(), agentsDir: process.env["AGENTS_DIR"] ?? DEFAULT_AGENTS_DIR, config: threadsConfigFrom(process.env), maxAttempts: 1, onUsage: (row) => recordUsage(db, row) });

const { plans } = await acts.threadsLink(run);
const outcomes = [];
const failures = [];
for (const p of process.argv.includes("--link-only") ? [] : plans) {
  try {
    outcomes.push(await acts.threadSynthesis(run, p));
  } catch (e) {
    failures.push({ threadId: p.threadId, error: String(e) });
  }
}
await acts.threadsFinish(run, { outcomes, failures });
const links = JSON.parse(store.get(store.find(run, THREAD_LINKS)!)) as { linker_ok: boolean; stories: { label: string; proposed_thread: number | null; outcome: string }[] };
console.log(JSON.stringify({ run, linkerOk: links.linker_ok, stories: links.stories.map((s) => [s.label, s.outcome, s.proposed_thread]), assignments: JSON.parse(store.get(store.find(run, THREAD_ASSIGNMENTS)!)) as unknown, synthesized: outcomes.length, auditFailures: outcomes.filter((o) => o.auditFailed).length, failures, cost: runCost(db, run, since) }, null, 2));
