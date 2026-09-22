import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

// A fresh database on the repo's own migrations, so the schema under test is production's.
const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;

export function freshDb(runIds: number[] = [300]): string {
  const path = join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");
  const db = new DatabaseSync(path);
  db.exec("CREATE TABLE digest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)");
  for (const id of runIds) db.prepare("INSERT INTO digest_runs (id, run_at) VALUES (?, ?)").run(id, "2026-09-18 10:25:40"); // node:sqlite enforces the FK the Python side leaves off
  for (const f of ["20260615100000_add_run_artifacts.sql", "20260729120000_unique_run_artifact_per_run.sql"]) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.close();
  return path;
}
