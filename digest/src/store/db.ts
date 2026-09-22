import { DatabaseSync } from "node:sqlite";
export const DEFAULT_DB_PATH = "/app/data/digest.db";
export const dbPath = (): string => process.env["DIGEST_DB_PATH"] ?? DEFAULT_DB_PATH;

export function openDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec("PRAGMA busy_timeout = 5000"); // the pipeline sets the same; WAL stays off (spec §5)
  return db;
}
