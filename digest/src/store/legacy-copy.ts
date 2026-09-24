import { createHash } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import type { Db, Sql } from "./db.js";

// The legacy SQLite file, table for table, into the `legacy` schema of the target database: the
// first half of the import (data-model design §5.1), which transform.sql then reshapes. Each value
// goes across as SQLite holds it: integers exact, reals to the last bit, times as their text. A value
// Postgres cannot hold as it is (a NUL byte, text that is not UTF-8) or of a storage class its column
// does not declare is refused, never altered, and the whole copy rolls back with it.

// The full-text index and yoyo's bookkeeping are not data.
export const LOADED = (name: string): boolean => !/^(sqlite_|_yoyo|yoyo_)/.test(name) && !name.startsWith("shown_narratives_fts");

const q = (id: string) => `"${id.replaceAll('"', '""')}"`;

// Read-only, and refused in WAL mode: commits may sit in a -wal file this would not read.
export function openLegacy(path: string): DatabaseSync {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const mode = (db.prepare("PRAGMA journal_mode").get() as { journal_mode: string }).journal_mode;
    if (mode.toLowerCase() === "wal") throw new Error(`${path} is in WAL mode: commits may sit in its -wal file; import an online-backup snapshot instead`);
    const enc = (db.prepare("PRAGMA encoding").get() as { encoding: string }).encoding;
    if (enc !== "UTF-8") throw new Error(`${path} holds its text as ${enc}; the copy reads UTF-8 only`);
    return db;
  } catch (e) {
    db.close();
    throw e;
  }
}

// The declared types the legacy file uses, what each becomes, and the storage classes each may hold.
// SQLite's REAL affinity keeps a whole number written to it as an integer, so REAL takes both.
type Pg = "bigint" | "double precision" | "text";
const TYPES: Record<string, { pg: Pg; holds: string[] }> = {
  INTEGER: { pg: "bigint", holds: ["integer"] },
  REAL: { pg: "double precision", holds: ["real", "integer"] },
  TEXT: { pg: "text", holds: ["text"] },
  DATETIME: { pg: "text", holds: ["text"] },
};

const tablesOf = (db: DatabaseSync): string[] =>
  (db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all() as { name: string }[]).map((t) => t.name).filter(LOADED);
function columnsOf(db: DatabaseSync, table: string): { name: string; type: string; pg: Pg }[] {
  const cols = db.prepare("SELECT name, upper(type) AS type FROM pragma_table_info(?) ORDER BY cid").all(table) as { name: string; type: string }[];
  return cols.map((c) => {
    const k = TYPES[c.type];
    if (!k) throw new Error(`${table}.${c.name}: declared ${c.type || "with no type"}; the copy knows ${Object.keys(TYPES).join(", ")}`);
    return { ...c, pg: k.pg };
  });
}

// What the file holds, exactly, and what the copy holds, computed apart: per table its rows, per
// column a digest of its values, and a digest of its rows (so a value moved to another row shows).
// A digest is the sum, mod 2^64 in two lanes, of each item's sha256: order-free, so neither side
// needs a key to line rows up. A value is encoded by its column's Postgres type: N for NULL, I and
// the digits of an integer, R and the 8 big-endian bytes of a double, T and the UTF-8 bytes of text.
export interface Fingerprint { [table: string]: { rows: number; columns: Record<string, string>; tuples: string } }

const MASK = (1n << 64n) - 1n;
class Digest {
  private a = 0n;
  private b = 0n;
  add(d: Buffer): void {
    this.a = (this.a + d.readBigUInt64BE(0)) & MASK;
    this.b = (this.b + d.readBigUInt64BE(8)) & MASK;
  }
  static of(a: bigint, b: bigint): string {
    return `${(a & MASK).toString(16).padStart(16, "0")}${(b & MASK).toString(16).padStart(16, "0")}`;
  }
  hex(): string {
    return Digest.of(this.a, this.b);
  }
}
const sha = (b: Buffer) => createHash("sha256").update(b).digest();
const NULL = Buffer.from("N");
function encode(pg: Pg, v: unknown): Buffer {
  if (v === null) return NULL;
  if (pg === "bigint") return Buffer.concat([Buffer.from("I"), Buffer.from((v as bigint).toString())]);
  if (pg === "double precision") {
    const b = Buffer.alloc(9, "R");
    b.writeDoubleBE(Number(v), 1);
    return b;
  }
  return Buffer.concat([Buffer.from("T"), v as Uint8Array]);
}
const lengthOf = (e: Buffer) => {
  const b = Buffer.alloc(4);
  b.writeUInt32BE(e.length);
  return b;
};

// The file's side: its own read of every table, in the caller's snapshot.
export function fileFingerprint(db: DatabaseSync): Fingerprint {
  const out: Fingerprint = {};
  for (const table of tablesOf(db)) {
    const cols = columnsOf(db, table);
    const select = db.prepare(`SELECT ${cols.map((c, i) => `${c.pg === "text" ? `CAST(${q(c.name)} AS BLOB)` : q(c.name)} AS v${i}`).join(", ")} FROM ${q(table)}`);
    select.setReadBigInts(true);
    const digests = cols.map(() => new Digest());
    const tuples = new Digest();
    let rows = 0;
    for (const row of select.iterate() as Iterable<Record<string, unknown>>) {
      const encoded = cols.map((c, i) => encode(c.pg, row[`v${i}`]));
      encoded.forEach((e, i) => digests[i]!.add(sha(e)));
      tuples.add(sha(Buffer.concat(encoded.flatMap((e) => [lengthOf(e), e]))));
      rows++;
    }
    out[table] = { rows, columns: Object.fromEntries(cols.map((c, i) => [c.name, digests[i]!.hex()])), tuples: tuples.hex() };
  }
  return out;
}

// Lane i of a digest column, summed: its 8 bytes as a signed bigint, whose sum is the unsigned one
// mod 2^64, which Digest.of takes.
const lane = (d: string, i: number) => `COALESCE(sum(('x' || encode(substr(${d}, ${1 + 8 * i}, 8), 'hex'))::bit(64)::bigint::numeric), 0)::text`;

// The copy's side, computed in Postgres over what it stored, for the tables and columns the file has.
export async function copyFingerprint(db: Sql, like: Fingerprint, schema = "legacy"): Promise<Fingerprint> {
  const out: Fingerprint = {};
  for (const table of Object.keys(like)) {
    const cols = await db.all<{ name: string; pg: Pg }>("SELECT column_name AS name, data_type AS pg FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", [schema, table]);
    if (!cols.length) continue;
    const enc = (c: { name: string; pg: Pg }) => {
      const v = q(c.name);
      const tagged = c.pg === "bigint" ? `'\\x49'::bytea || convert_to(${v}::text, 'UTF8')` : c.pg === "double precision" ? `'\\x52'::bytea || float8send(${v})` : `'\\x54'::bytea || convert_to(${v}, 'UTF8')`;
      return `CASE WHEN ${v} IS NULL THEN '\\x4e'::bytea ELSE ${tagged} END`;
    };
    const digests = [...cols.map((_, i) => `d${i}`), "t"];
    const row = (await db.one<Record<string, string>>(
      `WITH e AS (SELECT ${cols.map((c, i) => `${enc(c)} AS e${i}`).join(", ")} FROM ${q(schema)}.${q(table)}),
            d AS (SELECT ${cols.map((_, i) => `sha256(e${i}) AS d${i}`).join(", ")}, sha256(${cols.map((_, i) => `int4send(length(e${i})) || e${i}`).join(" || ")}) AS t FROM e)
       SELECT count(*)::text AS n, ${digests.flatMap((d) => [0, 1].map((i) => `${lane(d, i)} AS ${d}_${i}`)).join(", ")} FROM d`,
    ))!;
    const hex = (d: string) => Digest.of(BigInt(row[`${d}_0`]!), BigInt(row[`${d}_1`]!));
    out[table] = { rows: Number(row["n"]), columns: Object.fromEntries(cols.map((c, i) => [c.name, hex(`d${i}`)])), tuples: hex("t") };
  }
  return out;
}

// Every difference between the two, as readable lines.
export function fingerprintDiff(file: Fingerprint, copy: Fingerprint): string[] {
  const out: string[] = [];
  for (const [table, want] of Object.entries(file)) {
    const got = copy[table];
    if (!got || got.rows !== want.rows) {
      out.push(`${table}: ${want.rows} rows in the file, ${got ? got.rows : "no table"} copied`);
      continue;
    }
    const cols = Object.keys(want.columns).filter((c) => got.columns[c] !== want.columns[c]);
    for (const c of cols) out.push(`${table}.${c}: ${c in got.columns ? "the values differ" : "no such column"}`);
    if (!cols.length && got.tuples !== want.tuples) out.push(`${table}: the same values, in different rows`);
  }
  return out;
}

const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

// One column value as the text Postgres parses back to the same value: a bigint's digits, a double's
// shortest round-trip form, a string checked for what Postgres would refuse or SQLite never meant.
function param(v: unknown, at: () => string): string | null {
  if (v === null) return null;
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "number") return Object.is(v, -0) ? "-0" : String(v);
  const bytes = v as Uint8Array;
  if (bytes.includes(0)) throw new Error(`${at()}: a NUL byte, which Postgres text cannot hold`);
  try {
    return utf8.decode(bytes);
  } catch {
    throw new Error(`${at()}: not UTF-8`);
  }
}

async function copyTable(file: DatabaseSync, pg: Sql, table: string, batch: { rows: number; chars: number }): Promise<number> {
  const cols = columnsOf(file, table);
  await pg.exec(`CREATE TABLE legacy.${q(table)} (${cols.map((c) => `${q(c.name)} ${c.pg}`).join(", ")})`);
  // Text is read as its bytes, so a value that is not UTF-8 is seen rather than replaced on decode.
  const select = file.prepare(
    `SELECT rowid AS r${cols.map((c, i) => `, typeof(${q(c.name)}) AS t${i}, ${c.pg === "text" ? `CAST(${q(c.name)} AS BLOB)` : q(c.name)} AS v${i}`).join("")} FROM ${q(table)}`,
  );
  select.setReadBigInts(true);
  const insert = `INSERT INTO legacy.${q(table)} (${cols.map((c) => q(c.name)).join(", ")}) SELECT * FROM unnest(${cols.map((c, i) => `$${i + 1}::${c.pg}[]`).join(", ")})`;
  let arrays: (string | null)[][] = cols.map(() => []);
  let [pending, chars, total] = [0, 0, 0];
  const flush = async () => {
    if (pending) await pg.run(insert, arrays);
    arrays = cols.map(() => []);
    total += pending;
    [pending, chars] = [0, 0];
  };
  for (const row of select.iterate() as Iterable<Record<string, unknown>>) {
    for (const [i, c] of cols.entries()) {
      const at = () => `${table}.${c.name} row ${String(row["r"])}`;
      const kind = row[`t${i}`] as string;
      if (kind !== "null" && !TYPES[c.type]!.holds.includes(kind)) throw new Error(`${at()}: a ${kind} value in a column declared ${c.type}`);
      const v = param(row[`v${i}`], at);
      arrays[i]!.push(v);
      chars += v?.length ?? 0;
    }
    if (++pending >= batch.rows || chars >= batch.chars) await flush();
  }
  await flush();
  return total;
}

// Every loaded table into a new `legacy` schema, in one transaction on each side: one read snapshot
// of the file (its fingerprint and every row), one commit in Postgres, or nothing.
export async function copyLegacy(pg: Db, path: string, opts: { batchRows?: number; batchChars?: number } = {}): Promise<{ tables: string[]; rows: number; fingerprint: Fingerprint }> {
  const file = openLegacy(path);
  try {
    file.exec("BEGIN");
    const tables = tablesOf(file);
    for (const t of tables) columnsOf(file, t); // refuses a declared type it does not know, before any work
    const fingerprint = fileFingerprint(file);
    const batch = { rows: opts.batchRows ?? 2000, chars: opts.batchChars ?? 2 * 1024 * 1024 };
    let rows = 0;
    // AUTOINCREMENT's high-water marks, which max(id) understates once rows were deleted: the import
    // continues each identity after them (import.ts), so no id SQLite handed out is handed out again.
    const hasSequence = file.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'").get() !== undefined;
    const sequences = hasSequence ? (file.prepare("SELECT name, seq FROM sqlite_sequence").all() as { name: string; seq: number }[]) : [];
    await pg.tx(async (t) => {
      await t.exec("CREATE SCHEMA legacy");
      for (const table of tables) rows += await copyTable(file, t, table, batch);
      await t.exec("CREATE TABLE legacy.sqlite_sequence (name text PRIMARY KEY, seq bigint NOT NULL)");
      for (const s of sequences) await t.run("INSERT INTO legacy.sqlite_sequence (name, seq) VALUES ($1, $2)", [s.name, s.seq]);
    });
    return { tables, rows, fingerprint };
  } finally {
    file.close();
  }
}
