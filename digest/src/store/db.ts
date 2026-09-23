import pg from "pg";

// The product database is Postgres (data-model design, top). DIGEST_DATABASE_URL names it; tests
// register in-process PGlite databases under `pglite:` keys (store/test-db.ts).
export const DEFAULT_DB_URL = "postgres://digest@localhost:5432/digest";
export const dbUrl = (): string => process.env["DIGEST_DATABASE_URL"] ?? DEFAULT_DB_URL;

export type Row = Record<string, unknown>;
// Statements with $n parameters. exec runs a script of several statements and takes none.
export interface Sql {
  all<T = Row>(text: string, params?: unknown[]): Promise<T[]>;
  one<T = Row>(text: string, params?: unknown[]): Promise<T | undefined>;
  run(text: string, params?: unknown[]): Promise<number>;
  exec(text: string): Promise<void>;
}
export interface Db extends Sql {
  // One transaction. With `lock`, it first takes a transaction-scoped advisory lock on that name,
  // so a check-then-write on the same thing from two attempts runs one after the other.
  tx<T>(fn: (t: Sql) => Promise<T>, lock?: string): Promise<T>;
}

// Times come back as the UTC text the pipeline has always compared ("YYYY-MM-DD HH:MM:SS"), dates as
// "YYYY-MM-DD", bigints (ids, counts) as numbers. Every session runs in UTC.
export function utcText(v: string): string {
  const iso = v.replace(" ", "T");
  const d = new Date(/(?:[+-]\d\d(?::?\d\d)?|Z)$/.test(iso) ? iso.replace(/([+-]\d\d)$/, "$1:00") : `${iso}Z`);
  if (Number.isNaN(d.getTime())) throw new Error(`not a timestamp: ${v}`);
  return d.toISOString().slice(0, 19).replace("T", " ");
}
export const OIDS = { INT8: 20, NUMERIC: 1700, DATE: 1082, TIMESTAMP: 1114, TIMESTAMPTZ: 1184 } as const;
export const PARSERS: Record<number, (v: string) => unknown> = {
  [OIDS.INT8]: Number,
  [OIDS.NUMERIC]: Number,
  [OIDS.DATE]: (v) => v,
  [OIDS.TIMESTAMP]: utcText,
  [OIDS.TIMESTAMPTZ]: utcText,
};
const pgTypes = {
  getTypeParser: (oid: number, format?: string): ((v: string) => unknown) => PARSERS[oid] ?? (pg.types.getTypeParser(oid, format as "text") as (v: string) => unknown),
};

const LOCK = "SELECT pg_advisory_xact_lock(hashtext($1))";

function sqlOn(q: (text: string, params?: unknown[]) => Promise<{ rows: Row[]; rowCount: number | null }>, exec: (text: string) => Promise<unknown>): Sql {
  return {
    all: async <T>(text: string, params?: unknown[]) => (await q(text, params)).rows as T[],
    one: async <T>(text: string, params?: unknown[]) => (await q(text, params)).rows[0] as T | undefined,
    run: async (text: string, params?: unknown[]) => (await q(text, params)).rowCount ?? 0,
    exec: async (text: string) => {
      await exec(text);
    },
  };
}

function poolDb(url: string): Db {
  const pool = new pg.Pool({ connectionString: url, types: pgTypes, options: "-c TimeZone=UTC", max: 8 });
  // An idle client's connection can drop (a Postgres restart); the pool discards that client and the
  // next query opens a new one. Unhandled, the pool's error event would take the worker down.
  pool.on("error", (e) => console.error(`an idle Postgres connection was lost; the pool replaces it: ${e.message}`));
  const base = sqlOn((text, params) => pool.query(text, params), (text) => pool.query(text));
  return {
    ...base,
    tx: async (fn, lock) => {
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        if (lock !== undefined) await client.query(LOCK, [lock]);
        const out = await fn(sqlOn((text, params) => client.query(text, params), (text) => client.query(text)));
        await client.query("COMMIT");
        return out;
      } catch (e) {
        await client.query("ROLLBACK").catch(() => undefined);
        throw e;
      } finally {
        client.release();
      }
    },
  };
}

// A database that is not a URL: tests register PGlite under a key.
const registered = new Map<string, () => Db>();
export function registerDb(key: string, make: () => Db): void {
  registered.set(key, make);
}

// One pool per URL for the life of the process: activities open it per call and never close it.
const pools = new Map<string, Db>();
export function openDb(url: string): Db {
  const test = registered.get(url);
  if (test) return test();
  let db = pools.get(url);
  if (!db) pools.set(url, (db = poolDb(url)));
  return db;
}
