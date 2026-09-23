import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { coherenceKindCounts, getRunHealth, REQUIRED_KEYS, violations, type RunHealth } from "./run-health.js";

const healthy = (over: Partial<RunHealth> = {}): RunHealth => ({
  run_id: 245,
  shipped: 15,
  stages: 9,
  artifacts: 13,
  recipients: 11,
  broadcasting: true,
  thread_continuations: 5,
  threads_available: 55,
  threads_enabled: true,
  batches_lost: 0,
  stories_dropped_at_write: 0,
  title_only_fallback: 0,
  dropped_continuations: 0,
  linker_ok: true,
  blanked_why: 0,
  must_know_shipped: 5,
  fulltext_outcome: "completed",
  fulltext_tasks: 40,
  fulltext_extracted: 31,
  usage_rows_dropped: 0,
  repair_outcome: null,
  repair_detail: null,
  ...over,
});
const codes = (h: RunHealth) => violations(h).map((v) => v.split(":")[0]);

describe("violations", () => {
  it("a healthy run reports nothing", () => {
    expect(violations(healthy())).toEqual([]);
  });
  it.each([
    ["ZERO_STORIES", { shipped: 0 }],
    ["ZERO_RECIPIENTS", { recipients: 0 }],
    ["NO_USAGE_RECORDED", { stages: 0 }],
    ["NO_ARTIFACTS", { artifacts: 0 }],
    ["USAGE_ROWS_LOST", { usage_rows_dropped: 2 }],
    ["DEGRADED_CLUSTERING", { batches_lost: 1 }],
    ["STORIES_DROPPED_AT_WRITE", { stories_dropped_at_write: 1 }],
    ["BLANKED_WHY_IT_MATTERS", { blanked_why: 2 }],
    ["FULLTEXT_TOTAL_LOSS", { fulltext_extracted: 0 }],
    ["REPAIR_SPEC_ERROR", { repair_outcome: "spec_error" }],
    ["NO_THREAD_CONTINUATIONS", { thread_continuations: 0 }],
  ] as [string, Partial<RunHealth>][])("%s fires on its trigger", (code, broken) => {
    expect(codes(healthy(broken))).toEqual([code]);
  });
  it("zero recipients is silent when the run was not broadcasting, and an unknown count is not zero", () => {
    expect(violations(healthy({ recipients: 0, broadcasting: false }))).toEqual([]);
    expect(violations(healthy({ recipients: null }))).toEqual([]);
  });
  it("the guarded rules cannot judge without their artifact", () => {
    expect(violations(healthy({ batches_lost: null, stories_dropped_at_write: null, blanked_why: null, usage_rows_dropped: null, fulltext_tasks: null, fulltext_extracted: null }))).toEqual([]);
  });
  it("one blanked story is the designed fallback, not an alert", () => {
    expect(violations(healthy({ blanked_why: 1, must_know_shipped: 2 }))).toEqual([]);
  });
  it("rounds the blanked rate half to even, as Python's format does", () => {
    expect(violations(healthy({ blanked_why: 5, must_know_shipped: 8 }))).toEqual(["BLANKED_WHY_IT_MATTERS: 5 of 8 must_know stories (62%) went out with no why_it_matters"]);
    expect(violations(healthy({ blanked_why: 3, must_know_shipped: 8 }))[0]).toContain("(38%)");
    expect(violations(healthy({ blanked_why: 3, must_know_shipped: null }))).toEqual(["BLANKED_WHY_IT_MATTERS: 3 of None must_know stories went out with no why_it_matters"]);
  });
  it("thread continuity is silent with nothing to continue or the layer switched off, and names a failed linker", () => {
    expect(violations(healthy({ thread_continuations: 0, threads_available: 0 }))).toEqual([]);
    expect(violations(healthy({ thread_continuations: 0, threads_enabled: false }))).toEqual([]);
    expect(violations(healthy({ thread_continuations: 0, linker_ok: false }))[0]).toMatch(/linker call itself failed$/);
    expect(violations(healthy({ thread_continuations: 0, linker_ok: null }))[0]).not.toMatch(/linker/);
  });
  it("quotes the run's own numbers the way the Python does", () => {
    expect(violations(healthy({ batches_lost: 1, title_only_fallback: 40 }))).toEqual(["DEGRADED_CLUSTERING: 1 extraction batch(es) returned nothing usable; 40 articles lost their entity tags"]);
    expect(violations(healthy({ fulltext_extracted: 0, fulltext_outcome: null }))).toEqual(["FULLTEXT_TOTAL_LOSS: fulltext extracted 0 of 40 candidate articles (worker unknown); stories fell back to CSV summaries"]);
    expect(violations(healthy({ repair_outcome: "spec_error" }))[0]).toMatch(/: no detail recorded$/);
  });
  it("a dropped continuation is reported elsewhere, never as a violation", () => {
    expect(violations(healthy({ dropped_continuations: 3 }))).toEqual([]);
  });
  it("a health record missing a key is itself the violation", () => {
    const h: Record<string, unknown> = { ...healthy() };
    delete h["threads_available"];
    delete h["batches_lost"];
    expect(violations(h as unknown as RunHealth)).toEqual(["MALFORMED_HEALTH: run health is missing ['batches_lost', 'threads_available']; invariants NOT evaluated"]);
    expect(REQUIRED_KEYS.size).toBe(18);
  });
});

const report = (results: unknown[]) => JSON.stringify({ results });
describe("coherenceKindCounts", () => {
  it("counts each named failed field by kind, and a fail naming none as one unlabelled", () => {
    expect(
      coherenceKindCounts(
        report([
          { pass: true, failed_fields: ["headline"] },
          { pass: false, failed_fields: ["Headline ", "summary"], failure_kinds: { headline: "contradicted", summary: "unsupported" } },
          { pass: false, failed_fields: ["why_it_matters"], failure_kinds: { why_it_matters: "made_up" } },
          { pass: false, failure_kinds: { summary: "contradicted" } },
          { pass: false, failed_fields: ["preheader"] },
          "not an object",
        ]),
      ),
    ).toEqual({ contradicted: 2, unsupported: 1, unlabelled: 2 });
  });
  it("is null when there is no readable report", () => {
    expect(coherenceKindCounts(null)).toBeNull();
    expect(coherenceKindCounts("")).toBeNull();
    expect(coherenceKindCounts("{not json")).toBeNull();
    expect(coherenceKindCounts('{"results": {}}')).toBeNull();
    expect(coherenceKindCounts("[]")).toBeNull();
  });
});

const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;
function migratedDb(): DatabaseSync {
  const db = new DatabaseSync(join(mkdtempSync(join(tmpdir(), "health-")), "digest.db"));
  for (const f of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).toSorted()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  return db;
}

describe("getRunHealth", () => {
  const opts = { broadcasting: true, threadsEnabled: true, usageRowsDropped: 0 };
  it("reads the counts and the health artifacts of the run", () => {
    const db = migratedDb();
    db.exec("INSERT INTO digest_runs (id, run_at) VALUES (9, '2026-09-01 10:00:00'), (10, '2026-09-02 10:00:00')");
    const art = db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (10, ?, ?)");
    art.run("cluster_health.json", JSON.stringify({ batches_lost: 1, title_only_fallback: 38 }));
    art.run("fulltext_health.json", JSON.stringify({ tasks: 40, extracted: 0, outcome: "timeout" }));
    art.run("selections.json", JSON.stringify({ must_know: [{ why_it_matters: " " }, { why_it_matters: "x" }, {}], should_know: [] }));
    art.run("write_branches.json", JSON.stringify({ dropped: [{ index: 3 }] }));
    art.run("thread_links.json", JSON.stringify({ linker_ok: false, stories: [{ refused: "already_claimed" }, {}] }));
    art.run("repair_health.json", JSON.stringify({ outcome: "spec_error", detail: "repair.md" }));
    db.exec("INSERT INTO shown_narratives (headline, tier, run_id) VALUES ('a', 'must_know', 10), ('a', 'must_know', 10), ('b', 'should_know', 10)");
    db.exec("INSERT INTO run_usage (run_id, subagent, model) VALUES (10, 'select', 'm'), (10, 'write', 'm'), (10, 'write', 'm')");
    db.exec("INSERT INTO threads (id, label, status, first_run_id, last_run_id) VALUES (1, 's', 'active', 9, 9), (2, 't', 'active', 10, 10)");
    db.exec("INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) VALUES (1, 9, 's', NULL), (1, 10, 's', 0.9), (2, 10, 't', NULL)");
    const h = getRunHealth(db, 10, opts);
    expect(h).toEqual({
      run_id: 10, shipped: 2, stages: 2, artifacts: 6, recipients: null, thread_continuations: 1, threads_available: 1,
      broadcasting: true, usage_rows_dropped: 0, threads_enabled: true, batches_lost: 1, title_only_fallback: 38,
      fulltext_tasks: 40, fulltext_extracted: 0, fulltext_outcome: "timeout", blanked_why: 2, must_know_shipped: 3,
      dropped_continuations: 1, linker_ok: false, repair_outcome: "spec_error", repair_detail: "repair.md", stories_dropped_at_write: 1,
    });
  });
  it("a malformed artifact reads as cannot-judge instead of blanking every invariant", () => {
    const db = migratedDb();
    db.exec("INSERT INTO digest_runs (id) VALUES (10)");
    const art = db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (10, ?, ?)");
    art.run("cluster_health.json", "{truncated");
    art.run("thread_links.json", JSON.stringify({ linker_ok: "yes", stories: ["already_claimed"] }));
    art.run("selections.json", JSON.stringify({ must_know: {} }));
    const h = getRunHealth(db, 10, opts);
    expect(h).toMatchObject({ batches_lost: null, dropped_continuations: null, linker_ok: null, blanked_why: null, must_know_shipped: null, artifacts: 3 });
  });
});
