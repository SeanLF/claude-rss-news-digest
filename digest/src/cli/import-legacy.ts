// usage: import-legacy check-empty SRC | finish SRC
// The in-database half of bin/import-legacy, against DIGEST_DATABASE_URL; SRC is the SQLite file,
// read-only. check-empty refuses a database that holds anything, and a WAL-mode file; finish runs
// after pgloader has loaded SRC into public: it holds that load to the file's own fingerprint, moves
// it aside, builds the product schema with dbmate, copies across, verifies the copy against the
// legacy tables (exit 1 on any difference, legacy kept for inspection), and drops them.
import { dbUrl, openDb } from "../store/db.js";
import { assertEmpty, dropLegacy, fingerprintDiff, legacyFingerprint, moveAside, sqliteFingerprint, transform, verify } from "../store/import.js";
import { migrate } from "../store/schema.js";

const db = openDb(dbUrl());
const [step, src] = process.argv.slice(2);
if (!src) {
  console.error("usage: import-legacy check-empty SRC | finish SRC");
  process.exit(2);
}
const t0 = Date.now();
const ms = () => `${Date.now() - t0} ms`;
if (step === "check-empty") {
  await assertEmpty(db);
  sqliteFingerprint(src); // refuses a WAL-mode file
  console.log("import-legacy: the target database is empty");
} else if (step === "finish") {
  const file = sqliteFingerprint(src);
  const lost = fingerprintDiff(file, await legacyFingerprint(db, file, "public"));
  if (lost.length) {
    for (const l of lost) console.error(`FAIL load ${l}`);
    console.error("import-legacy: pgloader's load does not match the file; nothing was copied");
    process.exit(1);
  }
  console.log(`import-legacy: the load matches the file, ${Object.keys(file).length} tables (${ms()})`);
  await moveAside(db);
  migrate(dbUrl());
  console.log(`import-legacy: schema built (${ms()})`);
  await transform(db);
  console.log(`import-legacy: copied (${ms()})`);
  const checks = await verify(db);
  for (const c of checks) console.log(`${c.broken === 0 ? "ok  " : "FAIL"} ${c.name}${c.broken ? ` (${c.broken} broken)` : ""}`);
  if (checks.some((c) => c.broken !== 0)) {
    console.error("import-legacy: the copy does not match the legacy tables; the `legacy` schema is kept for inspection");
    process.exit(1);
  }
  await dropLegacy(db);
  await db.exec("VACUUM ANALYZE");
  const size = await db.one<{ size: string }>("SELECT pg_size_pretty(pg_database_size(current_database())) AS size");
  console.log(`import-legacy: done in ${ms()}, database ${size!.size}`);
} else {
  console.error("usage: import-legacy check-empty | finish");
  process.exit(2);
}
process.exit(0); // the pool would otherwise hold the process open
