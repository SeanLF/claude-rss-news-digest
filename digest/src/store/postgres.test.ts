import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MockActivityEnvironment } from "@temporalio/testing";
import { describe, expect, it } from "vitest";
import { runActivities } from "../activities/run.js";
import { ArtifactStore } from "./artifacts.js";
import { openDb } from "./db.js";
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

  it("two claims on one day's send at once: exactly one row, the other refused by its key", async () => {
    const url = await freshDatabase();
    const db = openDb(url);
    await db.exec("INSERT INTO runs (id, started_at) VALUES (1, '2026-09-18 10:25:00+00'); INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-18', 1, 1, '')");
    const claim = () => db.tx((t) => t.run("INSERT INTO sends (issue_date, run_id, revision, status) VALUES ('2026-09-18', 1, 1, 'claimed')"));
    const results = await Promise.allSettled([claim(), claim()]);
    expect(results.map((r) => r.status).toSorted()).toEqual(["fulfilled", "rejected"]);
  });
});
