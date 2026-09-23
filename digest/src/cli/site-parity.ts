// usage: site-parity record BASE_URL OUT_DIR
// Records a running server's answer to every request in site/parity/requests.ts, one JSON file per
// request plus manifest.json (capture time, font path, and RECORD_PROVENANCE: the source database and
// commit the goldens came from). Run against the Rust circulation server by bin/site-parity-record.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { capture, manifest, toRequest } from "../site/parity/harness.js";

const [step, base, out] = process.argv.slice(2);
if (step !== "record" || !base || !out) {
  console.error("usage: site-parity record BASE_URL OUT_DIR");
  process.exit(2);
}
const m = manifest();
mkdirSync(out, { recursive: true });
const capturedAt = new Date().toISOString();
// The font's path carries a hash of its bytes, which only the server computes; every page links it.
const index = await (await fetch(`${base}/`, { headers: m.headers })).text();
const fontPath = /\/assets\/fonts\/source-serif-4\.[0-9a-f]{8}\.woff2/.exec(index)?.[0];
if (!fontPath) {
  console.error("site-parity: no font link on the index page");
  process.exit(1);
}
let failed = 0;
for (const e of m.requests) {
  try {
    for (let i = 1; i < (e.repeat ?? 1); i++) await (await fetch(toRequest(base, m, e, fontPath))).arrayBuffer();
    const answer = await capture(await fetch(toRequest(base, m, e, fontPath)));
    writeFileSync(join(out, `${e.name}.json`), `${JSON.stringify({ name: e.name, ...answer }, null, 1)}\n`);
  } catch (err) {
    failed++;
    console.error(`site-parity: ${e.name}: ${String(err)}`);
  }
}
const provenance: unknown = JSON.parse(process.env["RECORD_PROVENANCE"] ?? "{}");
writeFileSync(join(out, "manifest.json"), `${JSON.stringify({ capturedAt, finishedAt: new Date().toISOString(), base, fontPath, provenance, requests: m.requests.length, failed }, null, 1)}\n`);
console.log(`site-parity: recorded ${m.requests.length - failed} of ${m.requests.length} into ${out}`);
process.exit(failed ? 1 : 0);
