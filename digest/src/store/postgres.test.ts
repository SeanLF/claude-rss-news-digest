import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { MockActivityEnvironment } from "@temporalio/testing";
import { describe, expect, it } from "vitest";
import { runActivities } from "../activities/run.js";
import { ArtifactStore } from "./artifacts.js";
import { openDb } from "./db.js";
import { copyFingerprint, copyLegacy, fingerprintDiff } from "./legacy-copy.js";
import { migrate } from "./schema.js";

// What PGlite cannot show: it is one connection, so every transaction runs alone and a lock is never
// contended. These run against the real Postgres CI starts beside the tests (docker-compose digest-pg,
// the box's image and major), each in a database of its own.
const ADMIN = process.env["DIGEST_TEST_DATABASE_URL"];

async function freshDatabase(): Promise<string> {
  const name = `t_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  await openDb(ADMIN!).exec(`CREATE DATABASE ${name}`);
  const url = ADMIN!.replace(/\/[^/?]+(\?|$)/, `/${name}$1`);
  migrate(url);
  return url;
}

// A double's IEEE bits, as Postgres's float8send gives them.
function bits(x: number): string {
  const b = Buffer.alloc(8);
  b.writeDoubleBE(x);
  return b.toString("hex");
}

describe.skipIf(!ADMIN)("on real Postgres", () => {
  it("the migration applies, and the schema's version is recorded", async () => {
    const url = await freshDatabase();
    expect(await openDb(url).all("SELECT version FROM schema_migrations")).toEqual([{ version: "20260923200000" }]);
    expect((await openDb(url).one<{ v: string }>("SELECT current_setting('server_version') AS v"))!.v).toMatch(/^18\./);
  });

  it("five executions starting the same day at once make one run, and the rest are refused", async () => {
    const url = await freshDatabase();
    const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
    writeFileSync(sourcesFile, JSON.stringify([{ id: "f", name: "F", url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global" }]));
    const acts = runActivities({ store: new ArtifactStore(url), dbUrl: url, sourcesFile });
    const start = (exec: string) => new MockActivityEnvironment({ workflowExecution: { workflowId: `digest-${exec}`, runId: exec } }).run(() => acts.startRun({ runDate: "" }));
    const results = await Promise.allSettled(["a", "b", "c", "d", "e"].map(start));
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
    expect(results.filter((r): r is PromiseRejectedResult => r.status === "rejected").map((r) => (r.reason as { type?: string }).type)).toEqual(["AlreadyRan", "AlreadyRan", "AlreadyRan", "AlreadyRan"]);
    expect(await openDb(url).one("SELECT count(*) AS n FROM runs")).toEqual({ n: 1 });
  });

  // The legacy copy through node-postgres, whose array serialization the PGlite tests never reach.
  it("copies the values an array literal could mangle, and doubles to the bit", async () => {
    const url = await freshDatabase();
    const path = join(mkdtempSync(join(tmpdir(), "legacy-")), "digest.db");
    const file = new DatabaseSync(path);
    const texts = ["", "NULL", "null", '"NULL"', "\\N", '"', "\\", '\\"', "{", "}", ",", "{a,b}", " lead", "trail ", "a\nb", "a\r\nb", "\t", "\uFEFFbom", "'", "😀", "\u2028", "é ✓"];
    const reals = [5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, 1e21, 1e-7, 1 / 3, 2 ** 53 + 2, 0.30000000000000004, Infinity, -Infinity];
    file.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, s TEXT, r REAL, i INTEGER)");
    const insert = file.prepare("INSERT INTO t VALUES (?, ?, ?, ?)");
    for (const [n, s] of texts.entries()) insert.run(n + 1, s, reals[n % reals.length]!, n === 0 ? 9223372036854775807n : n === 1 ? -9223372036854775808n : n);
    insert.run(100, null, null, null);
    file.close();
    const db = openDb(url);
    const copy = await copyLegacy(db, path, { batchRows: 5 });
    expect(fingerprintDiff(copy.fingerprint, await copyFingerprint(db, copy.fingerprint))).toEqual([]);
    const rows = await db.all<{ s: string | null; bits: string | null; i: string | null }>("SELECT s, encode(float8send(r), 'hex') AS bits, i::text AS i FROM legacy.t ORDER BY id");
    expect(rows.map((r) => r.s)).toEqual([...texts, null]);
    expect(rows.map((r) => r.bits)).toEqual([...texts.map((_, n) => bits(reals[n % reals.length]!)), null]);
    expect(rows.slice(0, 3).map((r) => r.i)).toEqual(["9223372036854775807", "-9223372036854775808", "2"]);
  });

  it("two claims on one day's send at once: exactly one row, the other refused by its key", async () => {
    const url = await freshDatabase();
    const db = openDb(url);
    await db.exec("INSERT INTO runs (id, started_at) VALUES (1, '2026-09-18 10:25:00+00'); INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-18', 1, 1, '')");
    const claim = () => db.tx((t) => t.run("INSERT INTO sends (issue_date, run_id, revision, status) VALUES ('2026-09-18', 1, 1, 'claimed')"));
    const results = await Promise.allSettled([claim(), claim()]);
    expect(results.map((r) => r.status).toSorted()).toEqual(["fulfilled", "rejected"]);
  });
});
