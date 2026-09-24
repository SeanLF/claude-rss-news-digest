import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// schema.gen.ts is generated from the migrations (scripts/schema-types.sh, on a fresh database in
// ci-pg). Committed so the types are read without a database, and held here to what the migrations
// make now: a migration without `make schema-types` fails CI.
const ADMIN = process.env["DIGEST_TEST_DATABASE_URL"];
const ROOT = new URL("../../", import.meta.url).pathname;
const run = (...args: string[]) => spawnSync("sh", ["scripts/schema-types.sh", ...args], { cwd: ROOT, encoding: "utf8" });

describe.skipIf(!ADMIN)("the generated row types", () => {
  it("are what the migrations make (regenerate with `make schema-types`)", () => {
    const r = run("--verify");
    expect(r.status, `${r.stdout}${r.stderr}`).toBe(0);
  }, 60_000);

  it("fail the check once they drift from the migrations (the negative control)", () => {
    const drifted = join(mkdtempSync(join(tmpdir(), "schema-types-")), "schema.gen.ts");
    const committed = readFileSync(join(ROOT, "src/store/schema.gen.ts"), "utf8");
    expect(committed).toContain("markdown: string | null;");
    writeFileSync(drifted, committed.replace("markdown: string | null;", "markdown: string;"));
    expect(run("--verify", "--out-file", drifted).status).not.toBe(0);
  }, 60_000);
});
