import { PGlite } from "@electric-sql/pglite";
import { PARSERS, type Db, type Row, type Sql } from "./db.js";
import { upSections } from "./schema.js";

// Postgres in-process for tests: PGlite 0.5.8 is Postgres 18.3 (the box runs 18.6) and runs every
// feature the schema uses. One instance per test process, migrated once: an instance costs ~250 MiB
// resident and closing one does not give it back (measured), so tests empty it instead of cloning it.
let instance: Promise<PGlite> | undefined;
async function start(): Promise<PGlite> {
  const db = new PGlite({ parsers: PARSERS });
  await db.exec("SET TIME ZONE 'UTC'");
  for (const up of upSections()) await db.exec(up);
  return db;
}
export const pglite = (): Promise<PGlite> => (instance ??= start());

// Every table back to empty, identities from 1.
export async function emptyPglite(): Promise<PGlite> {
  const db = await pglite();
  const { rows } = await db.query<{ t: string }>("SELECT string_agg(quote_ident(tablename), ', ') AS t FROM pg_tables WHERE schemaname = 'public'");
  await db.exec(`TRUNCATE ${rows[0]!.t} RESTART IDENTITY CASCADE`);
  return db;
}

const LOCK = "SELECT pg_advisory_xact_lock(hashtext($1))";
type Querier = Pick<PGlite, "query" | "exec">;
function sqlOn(q: Querier): Sql {
  return {
    all: async <T>(text: string, params?: unknown[]) => (await q.query<Row>(text, params)).rows as T[],
    one: async <T>(text: string, params?: unknown[]) => (await q.query<Row>(text, params)).rows[0] as T | undefined,
    run: async (text: string, params?: unknown[]) => (await q.query(text, params)).affectedRows ?? 0,
    exec: async (text: string) => {
      await q.exec(text);
    },
  };
}

// PGlite is one connection: transactions queue behind each other, as they would on a pool.
export function pgliteDb(db: PGlite): Db {
  return {
    ...sqlOn(db),
    tx: (fn, lock) =>
      db.transaction(async (t) => {
        if (lock !== undefined) await t.query(LOCK, [lock]);
        return fn(sqlOn(t));
      }),
  };
}
