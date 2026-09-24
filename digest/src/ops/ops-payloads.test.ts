import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { migrate } from "../store/schema.js";

// bin/ops reads production over SSH by piping one of these psql scripts into psql inside the box's
// Postgres container. Two guarantees stand between them and production data: the role (digest_ro,
// SELECT only, digest_ro.sql) and the session (PGOPTIONS default_transaction_read_only=on, which
// bin/ops sends; newsroom/tests/test_ops.py holds the command to it). Each is shown here to refuse a
// write with the other one absent, against the Postgres CI runs beside the tests.
const ADMIN = process.env["DIGEST_TEST_DATABASE_URL"];
const OPS_DIR = new URL("../../db/ops/", import.meta.url).pathname;
const SUBCOMMANDS = ["run", "usage", "health", "artifacts", "artifact"] as const;
const READ_ONLY = "-c default_transaction_read_only=on";
const META_ALLOWED = ["\\set", "\\pset", "\\getenv", "\\bind", "\\g", "\\gset", "\\if", "\\else", "\\endif", "\\warn", "\\echo"];
const payload = (sub: string): string => readFileSync(`${OPS_DIR}${sub}.sql`, "utf8");

type Env = { PGOPTIONS?: string; OPS_RUN?: string; OPS_NAME?: string };
function psql(url: string, script: string, env: Env = {}) {
  const r = spawnSync("psql", ["-X", "-q", url, "-f", "-"], {
    input: script,
    encoding: "utf8",
    // Unset PGOPTIONS unless the case names it, so a case without the session guarantee has none.
    env: { PATH: process.env["PATH"], OPS_RUN: "", OPS_NAME: "", ...env },
  });
  if (r.error) throw r.error;
  return r;
}
const withDb = (url: string, name: string) => url.replace(/\/[^/?]+(\?|$)/, `/${name}$1`);
const asRole = (url: string, role: string, password: string) => url.replace(/\/\/[^@]+@/, `//${role}:${password}@`);

async function freshDatabase(): Promise<string> {
  const name = `ops_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const created = psql(ADMIN!, `CREATE DATABASE ${name}`);
  expect(created.stderr).toBe("");
  const url = withDb(ADMIN!, name);
  migrate(url);
  const seeded = psql(url, `\\set ON_ERROR_STOP on
INSERT INTO runs (id, started_at, status) VALUES (284, '2026-09-22 10:25Z', 'running'), (285, '2026-09-23 10:25Z', 'running');
INSERT INTO run_attempts (id, run_id, pipeline) VALUES (285, 285, 'temporal');
INSERT INTO artifacts (run_id, attempt_id, name, content, sha256) VALUES (285, 285, 'clusters.json', 'PAYLOAD-OK', 'x');
INSERT INTO model_calls (run_id, stage, request_model, api_cost_usd) VALUES (285, 'write', 'm', 0.5);
INSERT INTO source_fetches (run_id, source_id, is_success) VALUES (285, 'bbc', false);
`);
  expect(seeded.status, seeded.stderr).toBe(0);
  // The role as the box would have it, plus a password so this test can log in over TCP.
  const role = psql(url, `\\set ON_ERROR_STOP on\n${readFileSync(`${OPS_DIR}digest_ro.sql`, "utf8")}\nALTER ROLE digest_ro PASSWORD 'ro';\n`);
  expect(role.status, role.stderr).toBe(0);
  return url;
}

const count = (url: string, table: string) => psql(url, `SELECT count(*) FROM ${table}`).stdout.match(/\d+/)![0];
// A write placed before the payload's first query, so it is the first thing the session runs.
const withWrite = (script: string, write: string) => script.replace("\\getenv rid OPS_RUN", `${write}\n\\getenv rid OPS_RUN`);

describe("the ops payloads", () => {
  it.each(SUBCOMMANDS)("%s carries no statement that writes or changes the session", (sub) => {
    // A meta-command runs in psql, as root in the database's container on the box: \! is a shell,
    // \o and \w write files, \gexec runs query output as SQL. Only these may appear.
    const code = payload(sub)
      .split("\n")
      .filter((l) => !l.trimStart().startsWith("--"));
    const meta = code.join("\n").match(/\\[^\s:]+/g) ?? [];
    for (const m of meta) expect(META_ALLOWED, `${sub}: ${m}`).toContain(m);
    // What is left once the meta-command lines go is SQL the server runs.
    const sql = code
      .filter((l) => !l.trimStart().startsWith("\\"))
      .join("\n")
      .toLowerCase();
    for (const verb of ["insert", "update", "delete", "merge", "drop", "alter", "create", "truncate", "grant", "revoke", "copy", "set", "reset", "begin", "start", "commit", "do", "call", "lock", "vacuum", "attach"])
      expect(sql, `${sub}: ${verb}`).not.toMatch(new RegExp(`\\b${verb}\\b`));
  });
});

// Each case creates and migrates a database and spawns psql several times: ~1 s idle, over 5 s on a
// loaded host.
describe.skipIf(!ADMIN)("the ops payloads on real Postgres", { timeout: 30_000 }, () => {
  it("each reads the latest run as digest_ro in a read-only session", async () => {
    const ro = asRole(await freshDatabase(), "digest_ro", "ro");
    for (const sub of ["run", "usage", "health", "artifacts"] as const) {
      const r = psql(ro, payload(sub), { PGOPTIONS: READ_ONLY });
      expect(r.status, `${sub}: ${r.stderr}`).toBe(0);
      const out = JSON.parse(r.stdout) as { run_id: number; rows: unknown[] };
      expect(out.run_id).toBe(285);
      expect(out.rows).toHaveLength(1);
    }
    const run = JSON.parse(psql(ro, payload("run"), { PGOPTIONS: READ_ONLY, OPS_RUN: "284" }).stdout) as { run_id: number };
    expect(run.run_id).toBe(284);
  });

  it("an artifact name is a bound value: a hostile one finds nothing and changes nothing", async () => {
    const url = await freshDatabase();
    const ro = asRole(url, "digest_ro", "ro");
    const hostile = psql(ro, payload("artifact"), { PGOPTIONS: READ_ONLY, OPS_RUN: "285", OPS_NAME: "clusters.json' OR true; DROP TABLE runs; --" });
    expect(hostile.status).not.toBe(0);
    expect(hostile.stderr).toContain("no such artifact for run 285");
    expect(count(url, "runs")).toBe("2");
    const good = psql(ro, payload("artifact"), { PGOPTIONS: READ_ONLY, OPS_RUN: "285", OPS_NAME: "clusters.json" });
    expect(good.status, good.stderr).toBe(0);
    expect(good.stdout).toBe("PAYLOAD-OK"); // byte for byte: nothing appended
  });

  it("the session refuses a write from a role that may write", async () => {
    const url = await freshDatabase();
    const script = withWrite(payload("run"), "DELETE FROM artifacts;");
    const refused = psql(url, script, { PGOPTIONS: READ_ONLY });
    expect(refused.status).not.toBe(0);
    expect(refused.stderr).toContain("cannot execute DELETE in a read-only transaction");
    expect(count(url, "artifacts")).toBe("1");
    // The control: without the session setting, the same script as the same role does delete.
    expect(psql(url, script).status).toBe(0);
    expect(count(url, "artifacts")).toBe("0");
  });

  it("digest_ro refuses a write even from a session that turns read-only off", async () => {
    const url = await freshDatabase();
    const ro = asRole(url, "digest_ro", "ro");
    for (const write of ["DELETE FROM artifacts;", "INSERT INTO runs DEFAULT VALUES;", "UPDATE runs SET git_sha = 'x';", "CREATE TABLE planted (x int);"]) {
      const r = psql(ro, withWrite(payload("run"), `SET default_transaction_read_only = off;\n${write}`));
      expect(r.status, write).not.toBe(0);
      expect(r.stderr, write).toMatch(/permission denied/);
    }
    expect(count(url, "artifacts")).toBe("1");
  });

  it("digest_ro reads a table a later migration adds", async () => {
    const url = await freshDatabase();
    expect(psql(url, "CREATE TABLE later (x int); INSERT INTO later VALUES (1);").status).toBe(0);
    const r = psql(asRole(url, "digest_ro", "ro"), "SELECT count(*) FROM later;");
    expect(r.stderr).toBe("");
    expect(r.stdout).toMatch(/\b1\b/);
  });
});
