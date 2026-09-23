import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { resolveBinary } from "dbmate";

// digest/db/migrations, from src/store and from dist/store alike.
export const MIGRATIONS_DIR = new URL("../../db/migrations/", import.meta.url).pathname;

// Applies every pending migration to the database at `url` with dbmate. The exit is checked here
// rather than through dbmate's own CLI wrapper, which exits 0 when the binary is killed.
export function migrate(url: string): void {
  const r = spawnSync(resolveBinary(), ["--url", url, "--migrations-dir", MIGRATIONS_DIR, "--no-dump-schema", "up"], { encoding: "utf8" });
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`dbmate up failed (${r.signal ?? `exit ${String(r.status)}`}): ${r.stderr.trim()}`);
}

// Every migration's up section, in order: what dbmate applies, for an in-process PGlite that dbmate
// cannot reach. dbmate's file format: `-- migrate:up` ... `-- migrate:down` ...
export function upSections(): string[] {
  return readdirSync(MIGRATIONS_DIR)
    .filter((f) => f.endsWith(".sql"))
    .toSorted()
    .map((f) => {
      const text = readFileSync(join(MIGRATIONS_DIR, f), "utf8");
      const up = text.indexOf("-- migrate:up");
      const down = text.indexOf("-- migrate:down");
      if (up < 0 || down < up) throw new Error(`${f}: no -- migrate:up section before -- migrate:down`);
      return text.slice(up + "-- migrate:up".length, down);
    });
}
