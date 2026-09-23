// usage: import-legacy SRC
// The import, against DIGEST_DATABASE_URL (data-model design §5.1). SRC is the legacy SQLite file, read
// only and in one snapshot. Refuses a database that holds anything and a WAL-mode file; copies every
// table into the `legacy` schema and holds that copy to the file's own fingerprint; builds the product
// schema with dbmate, copies across, verifies the copy against the legacy tables (exit 1 on any
// difference, legacy kept for inspection), and drops them.
import { dbUrl, openDb } from "../store/db.js";
import { assertEmpty, dropLegacy, transform, verify } from "../store/import.js";
import { copyFingerprint, copyLegacy, fingerprintDiff } from "../store/legacy-copy.js";
import { migrate } from "../store/schema.js";

const db = openDb(dbUrl());
const src = process.argv[2];
if (!src || process.argv.length > 3) {
  console.error("usage: import-legacy SRC");
  process.exit(2);
}
const t0 = Date.now();
const ms = () => `${Date.now() - t0} ms`;
const fail = (why: string) => {
  console.error(`import-legacy: ${why}; drop the database before a retry`);
  process.exit(1);
};

await assertEmpty(db);
const copy = await copyLegacy(db, src);
console.log(`import-legacy: copied ${copy.rows} rows of ${copy.tables.length} tables into legacy (${ms()})`);
const lost = fingerprintDiff(copy.fingerprint, await copyFingerprint(db, copy.fingerprint));
if (lost.length) {
  for (const l of lost) console.error(`FAIL copy ${l}`);
  fail("the legacy copy does not match the file");
}
console.log(`import-legacy: the copy matches the file (${ms()})`);
migrate(dbUrl());
console.log(`import-legacy: schema built (${ms()})`);
await transform(db);
console.log(`import-legacy: transformed (${ms()})`);
const checks = await verify(db);
for (const c of checks) console.log(`${c.broken === 0 ? "ok  " : "FAIL"} ${c.name}${c.broken ? ` (${c.broken} broken)` : ""}`);
if (checks.some((c) => c.broken !== 0)) fail("the copy does not match the legacy tables; the `legacy` schema is kept for inspection");
await dropLegacy(db);
await db.exec("VACUUM ANALYZE");
const size = await db.one<{ size: string }>("SELECT pg_size_pretty(pg_database_size(current_database())) AS size");
// maxRSS is in KiB: the peak of this process, the copy's batches included.
console.log(`import-legacy: done in ${ms()}, database ${size!.size}, peak RSS ${Math.round(process.resourceUsage().maxRSS / 1024)} MiB`);
process.exit(0); // the pool would otherwise hold the process open
