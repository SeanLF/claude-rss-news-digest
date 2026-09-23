import { copyFileSync, mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;
const fresh = () => join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");

// Every migration applied once per test process, then copied: applying them all per test is
// seconds on a slow CI box.
let template: string | undefined;
function migrated(): string {
  if (template) return template;
  const path = fresh();
  const db = new DatabaseSync(path);
  for (const f of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).toSorted()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.close();
  return (template = path);
}

// A database on every one of the repo's migrations, in order: the schema the web tier reads, for
// tests of the activities that write the tables it serves.
export function migratedDb(runs: { id: number; runAt: string }[] = []): string {
  const path = fresh();
  copyFileSync(migrated(), path);
  const db = new DatabaseSync(path);
  for (const r of runs) db.prepare("INSERT INTO digest_runs (id, run_at) VALUES (?, ?)").run(r.id, r.runAt);
  db.close();
  return path;
}
