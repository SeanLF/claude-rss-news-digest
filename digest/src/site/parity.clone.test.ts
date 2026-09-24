import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { openDb } from "../store/db.js";
import { type Answer, capture, compare, manifest, toRequest, type Verdict } from "./parity/harness.js";
import { sameDocument } from "./parity/document.js";
import { siteStore } from "./store.js";
import { testApp, testConfig } from "./testing.js";

// Host-only (make site-parity): the TypeScript site, in-process, against the recording's prod clone as
// bin/import-legacy imported it into SITE_PARITY_DATABASE_URL, held to the Rust server's goldens in
// SITE_PARITY_DIR/golden. The clock is the capture time, so the stats windows cover the same rows.
const DIR = process.env["SITE_PARITY_DIR"];
const DB_URL = process.env["SITE_PARITY_DATABASE_URL"];

function env(file: string): Record<string, string> {
  return Object.fromEntries(
    readFileSync(file, "utf8")
      .split("\n")
      .filter((l) => l.includes("="))
      .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
  );
}

describe.skipIf(!DIR || !DB_URL)("parity with the Rust server's recorded answers", () => {
  it("answers every request as the Rust server did, or diverges as the fork doc says", async () => {
    const m = manifest();
    const recorded = JSON.parse(readFileSync(`${DIR}/golden/manifest.json`, "utf8")) as { capturedAt: string; fontPath: string };
    const cfg = testConfig(env(new URL("./parity/site.env", import.meta.url).pathname));
    const app = testApp(siteStore(openDb(DB_URL!)), { cfg, now: () => new Date(recorded.capturedAt) });
    const verdicts: Verdict[] = [];
    for (const e of m.requests) {
      const golden = JSON.parse(readFileSync(`${DIR}/golden/${e.name}.json`, "utf8")) as Answer;
      let res: Response | undefined;
      for (let i = 0; i < (e.repeat ?? 1); i++) res = await app.fetch(toRequest("http://site", m, e, recorded.fontPath));
      verdicts.push(compare(e, golden, await capture(res!), sameDocument));
    }
    const failed = verdicts.filter((v) => !v.ok && !v.known);
    const known = verdicts.filter((v) => !v.ok && v.known);
    console.log(`site parity: ${verdicts.filter((v) => v.ok).length} of ${verdicts.length} equal (${verdicts.filter((v) => v.asDocument).length} of them as Markdown documents, not bytes), ${known.length} known divergences, ${failed.length} failures`);
    for (const v of known) console.log(`  known ${v.name} (${v.known}): ${v.diffs.join("; ")}`);
    for (const v of failed) console.log(`  FAIL ${v.name}: ${v.diffs.join("\n    ")}`);
    expect(failed.map((v) => v.name)).toEqual([]);
  });
});
