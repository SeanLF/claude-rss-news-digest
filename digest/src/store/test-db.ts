import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

// A fresh database on the repo's own migrations, so the schema under test is production's.
const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;

export function freshDb(runIds: number[] = [300]): string {
  const path = join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");
  const db = new DatabaseSync(path);
  db.exec("CREATE TABLE digest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at DATETIME DEFAULT CURRENT_TIMESTAMP, articles_kept INTEGER, articles_emailed INTEGER DEFAULT 0, completed_at DATETIME, git_sha TEXT, status TEXT NOT NULL DEFAULT 'running', error TEXT)");
  db.exec("CREATE TABLE fetched_articles (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, source_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, published TEXT, summary TEXT, fetched_at DATETIME DEFAULT (datetime('now', 'utc')))");
  db.exec("CREATE TABLE source_health (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, success INTEGER NOT NULL, error_message TEXT, recorded_at DATETIME DEFAULT (datetime('now', 'utc')), articles_fetched INTEGER, articles_kept INTEGER, run_id INTEGER)");
  for (const id of runIds) db.prepare("INSERT INTO digest_runs (id, run_at) VALUES (?, ?)").run(id, "2026-09-18 10:25:40"); // node:sqlite enforces the FK the Python side leaves off
  for (const f of ["20260615100000_add_run_artifacts.sql", "20260729120000_unique_run_artifact_per_run.sql"]) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.close();
  return path;
}
