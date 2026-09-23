import { createHash } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { PGlite } from "@electric-sql/pglite";
import { beforeAll, describe, expect, it } from "vitest";
import { PARSERS } from "./db.js";
import { copyFingerprint, copyLegacy, fingerprintDiff, openLegacy } from "./legacy-copy.js";
import { pgliteDb } from "./pglite.js";

// A legacy file: the SQLite declared types the prod file uses (INTEGER, REAL, TEXT, DATETIME).
function legacyFile(sql: string, wal = false): string {
  const path = join(mkdtempSync(join(tmpdir(), "legacy-")), "digest.db");
  const db = new DatabaseSync(path);
  if (wal) db.exec("PRAGMA journal_mode = WAL");
  db.exec(sql);
  db.close();
  return path;
}
const TABLES = `CREATE TABLE digest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at DATETIME, articles_kept INTEGER, git_sha TEXT);
CREATE TABLE dedup_log (id INTEGER PRIMARY KEY, similarity REAL NOT NULL, title TEXT);
CREATE TABLE _yoyo_migration (id TEXT);`;

// One instance for the file, emptied per test: a PGlite instance costs ~250 MiB and seconds to start.
let instance: PGlite | undefined;
async function fresh(): Promise<PGlite> {
  const pg = (instance ??= new PGlite({ parsers: PARSERS }));
  await pg.exec("DROP SCHEMA IF EXISTS legacy CASCADE");
  return pg;
}
// Started before the first test, whose 5 s would otherwise include PGlite's start on a loaded host.
beforeAll(async () => {
  await (instance ??= new PGlite({ parsers: PARSERS })).query("SELECT 1");
}, 60_000);
async function copied(path: string): Promise<{ pg: PGlite; copy: Awaited<ReturnType<typeof copyLegacy>> }> {
  const pg = await fresh();
  return { pg, copy: await copyLegacy(pgliteDb(pg), path) };
}
const rows = async (pg: PGlite, sql: string) => (await pg.query(sql)).rows;

describe("copyLegacy: the SQLite file into the legacy schema", () => {
  it("copies every loaded table with its values exact: big integers, reals to the last bit, times as the text they are", async () => {
    const path = legacyFile(`${TABLES}
      INSERT INTO digest_runs VALUES (1, '2026-09-10 10:25:00', 9007199254740993, 'a'), (2, NULL, NULL, 'é ✓ "q" \\ {x,y}');
      INSERT INTO dedup_log VALUES (1, 0.30000000000000004, 't'), (2, 0.35547812653826977, NULL), (3, 5, 'int in a REAL column');
      INSERT INTO _yoyo_migration VALUES ('m1');`);
    const { pg, copy } = await copied(path);
    expect(copy.tables).toEqual(["dedup_log", "digest_runs"]);
    expect(copy.rows).toBe(5);
    // Read back as text, so neither the JS number nor the parser can round anything.
    expect(await rows(pg, "SELECT id::text, run_at, articles_kept::text AS kept, git_sha FROM legacy.digest_runs ORDER BY id")).toEqual([
      { id: "1", run_at: "2026-09-10 10:25:00", kept: "9007199254740993", git_sha: "a" },
      { id: "2", run_at: null, kept: null, git_sha: 'é ✓ "q" \\ {x,y}' },
    ]);
    expect(await rows(pg, "SELECT similarity::text AS s FROM legacy.dedup_log ORDER BY id")).toEqual([{ s: "0.30000000000000004" }, { s: "0.35547812653826977" }, { s: "5" }]);
    expect(await rows(pg, "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'legacy' ORDER BY table_name, ordinal_position")).toEqual([
      { table_name: "dedup_log", column_name: "id", data_type: "bigint" },
      { table_name: "dedup_log", column_name: "similarity", data_type: "double precision" },
      { table_name: "dedup_log", column_name: "title", data_type: "text" },
      { table_name: "digest_runs", column_name: "id", data_type: "bigint" },
      { table_name: "digest_runs", column_name: "run_at", data_type: "text" },
      { table_name: "digest_runs", column_name: "articles_kept", data_type: "bigint" },
      { table_name: "digest_runs", column_name: "git_sha", data_type: "text" },
    ]);
    expect(fingerprintDiff(copy.fingerprint, await copyFingerprint(pgliteDb(pg), copy.fingerprint))).toEqual([]);
  });

  it("copies a table larger than one batch whole", async () => {
    const path = legacyFile(`${TABLES} WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 2500) INSERT INTO dedup_log SELECT i, i / 7.0, printf('t%d', i) FROM n;`);
    const pg = await fresh();
    const copy = await copyLegacy(pgliteDb(pg), path, { batchRows: 1000 });
    expect(await rows(pg, "SELECT count(*)::int AS n, count(DISTINCT id)::int AS d FROM legacy.dedup_log")).toEqual([{ n: 2500, d: 2500 }]);
    expect(fingerprintDiff(copy.fingerprint, await copyFingerprint(pgliteDb(pg), copy.fingerprint))).toEqual([]);
  });

  // Negative controls of the fingerprint, the one check against the file itself: each kind of change a
  // copy could make to what it stored must show, a sum-preserving or same-length one included.
  it.each([
    ["an integer off by a little", "UPDATE legacy.digest_runs SET articles_kept = articles_kept + 50 WHERE id = 1", ["digest_runs.articles_kept: the values differ"]],
    ["two integers changed, their sum kept", "UPDATE legacy.digest_runs SET id = CASE id WHEN 1 THEN 0 ELSE 3 END", ["digest_runs.id: the values differ"]],
    ["a double in its last bit", "UPDATE legacy.dedup_log SET similarity = 0.3000000000000001 WHERE id = 1", ["dedup_log.similarity: the values differ"]],
    ["text of the same length", "UPDATE legacy.digest_runs SET git_sha = 'b' WHERE id = 1", ["digest_runs.git_sha: the values differ"]],
    ["a value moved to another row", "UPDATE legacy.dedup_log SET title = CASE id WHEN 1 THEN 'int in a REAL column' WHEN 3 THEN 't' ELSE title END", ["dedup_log: the same values, in different rows"]],
    ["a lost row", "DELETE FROM legacy.dedup_log WHERE id = 2", ["dedup_log: 3 rows in the file, 2 copied"]],
    ["a NULL for an empty string", "UPDATE legacy.dedup_log SET title = '' WHERE id = 2", ["dedup_log.title: the values differ"]],
  ])("the fingerprint sees %s", async (_name, change, lines) => {
    const path = legacyFile(`${TABLES}
      INSERT INTO digest_runs VALUES (1, '2026-09-10 10:25:00', 40, 'a'), (2, NULL, 9007199254740993, 'c');
      INSERT INTO dedup_log VALUES (1, 0.30000000000000004, 't'), (2, 0.35547812653826977, NULL), (3, 5, 'int in a REAL column');`);
    const { pg, copy } = await copied(path);
    expect(fingerprintDiff(copy.fingerprint, await copyFingerprint(pgliteDb(pg), copy.fingerprint))).toEqual([]);
    await pg.exec(change);
    expect(fingerprintDiff(copy.fingerprint, await copyFingerprint(pgliteDb(pg), copy.fingerprint))).toEqual(lines);
  });

  // Negative controls: each refusal names the value, and nothing of the copy is left behind.
  it.each([
    ["a NUL byte in text", "INSERT INTO digest_runs (id, git_sha) VALUES (7, 'a' || char(0) || 'b')", /digest_runs\.git_sha row 7: a NUL byte/],
    ["text that is not UTF-8", "INSERT INTO digest_runs (id, git_sha) VALUES (7, CAST(X'61FF62' AS TEXT))", /digest_runs\.git_sha row 7: not UTF-8/],
    ["text in an INTEGER column", "INSERT INTO digest_runs (id, articles_kept) VALUES (7, 'many')", /digest_runs\.articles_kept row 7: a text value in a column declared INTEGER/],
    ["a blob", "INSERT INTO digest_runs (id, git_sha) VALUES (7, X'00FF')", /digest_runs\.git_sha row 7: a blob value in a column declared TEXT/],
    ["a column type the copy does not know", "CREATE TABLE story_feedback (id INTEGER, vote BOOLEAN)", /story_feedback\.vote: declared BOOLEAN/],
  ])("refuses %s", async (_name, sql, why) => {
    const pg = await fresh();
    await expect(copyLegacy(pgliteDb(pg), legacyFile(`${TABLES} ${sql};`))).rejects.toThrow(why);
    expect(await rows(pg, "SELECT count(*)::int AS n FROM pg_namespace WHERE nspname = 'legacy'")).toEqual([{ n: 0 }]);
  });

  it("refuses a file in WAL mode, whose last commits may not be in the file itself", () => {
    expect(() => openLegacy(legacyFile(TABLES, true))).toThrow(/WAL mode/);
  });

  it("never writes the file, nor leaves a journal beside it", async () => {
    const path = legacyFile(`${TABLES} INSERT INTO digest_runs (id) VALUES (1);`);
    const hash = () => createHash("sha256").update(readFileSync(path)).digest("hex");
    const [before, mtime] = [hash(), statSync(path).mtimeMs];
    await copied(path);
    expect([hash(), statSync(path).mtimeMs]).toEqual([before, mtime]);
    expect(readdirSync(dirname(path))).toEqual(["digest.db"]);
  });
});
