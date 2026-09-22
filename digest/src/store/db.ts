import { DatabaseSync } from "node:sqlite";
export function openDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec("PRAGMA busy_timeout = 5000"); // the pipeline sets the same; WAL stays off (spec §5)
  return db;
}
