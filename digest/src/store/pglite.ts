import { PGlite, type PGliteInterface, types } from "@electric-sql/pglite";
import { upSections } from "./schema.js";

// Postgres in-process for tests (PGlite 0.5.8 is Postgres 18.3; the box runs 18.6). Rows come back
// as the pipeline's pg pool returns them: bigint as a number, times and dates as their text. Passed
// with every query, since a clone keeps its source's data but not its options.
export const PARSERS = {
  [types.INT8]: (v: string) => Number(v),
  [types.TIMESTAMPTZ]: (v: string) => v,
  [types.TIMESTAMP]: (v: string) => v,
  [types.DATE]: (v: string) => v,
};

// Migrated once per test process; each test gets a clone of it, so no test sees another's rows.
let template: Promise<PGlite> | undefined;
async function migrated(): Promise<PGlite> {
  const db = new PGlite();
  for (const up of upSections()) await db.exec(up);
  return db;
}

export async function freshPglite(): Promise<PGliteInterface> {
  const db = await (await (template ??= migrated())).clone();
  await db.exec("SET TIME ZONE 'UTC'");
  return db;
}
