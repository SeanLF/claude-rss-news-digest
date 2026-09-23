import { openDb, registerDb } from "./db.js";
import { emptyPglite, pgliteDb } from "./pglite.js";
import { RESET_IDENTITIES } from "./schema.js";

// A database key for tests: the process's PGlite, emptied, holding the given runs. The previous key
// stops working, so a test that kept an older database fails loudly instead of reading this one.
let generation = 0;
export async function migratedDb(runs: { id: number; runAt: string }[] = []): Promise<string> {
  const pg = await emptyPglite();
  const key = `pglite:${++generation}`;
  const mine = generation;
  registerDb(key, () => {
    if (mine !== generation) throw new Error(`${key} was emptied for a newer test database`);
    return pgliteDb(pg);
  });
  const db = openDb(key);
  for (const r of runs) await db.run("INSERT INTO digest_runs (id, run_at) VALUES ($1, $2)", [r.id, r.runAt]);
  await db.exec(RESET_IDENTITIES);
  return key;
}

// The given runs, all on 2026-09-18.
export const freshDb = (runIds: number[] = [300]): Promise<string> => migratedDb(runIds.map((id) => ({ id, runAt: "2026-09-18 10:25:40" })));
