import { describe, expect, it } from "vitest";
import { openDb } from "./db.js";

// Host-only (make import-check): the production clone data/prod-20260923b.db (runs 1-305) imported by
// bin/import-legacy into IMPORTED_CLONE_URL, held to the numbers of the data-model design §5.1, each
// measured on the SQLite file by docs/2026-09-23-import-expectations.sql. The importer's own checks
// hold the copy against the legacy tables row by row; these hold it to what the design says the clone is.
const CLONE_URL = process.env["IMPORTED_CLONE_URL"];

// Columns renamed c0, c1, ...: several unnamed aggregates would otherwise share one key.
async function n(sql: string, width: number): Promise<number[]> {
  const cols = Array.from({ length: width }, (_, i) => `c${i}`);
  const row = (await openDb(CLONE_URL!).one<Record<string, unknown>>(`SELECT * FROM (${sql}) AS x(${cols.join(", ")})`))!;
  return cols.map((c) => Number(row[c]));
}

describe.skipIf(!CLONE_URL)("the prod clone, imported (§5.1)", () => {
  it.each([
    ["runs: all, completed, sent, unrecorded, failed, running", "SELECT count(*), sum((status = 'completed')::int), sum((outcome = 'sent')::int), sum((outcome = 'unrecorded')::int), sum((status = 'failed')::int), sum((status = 'running')::int) FROM runs", [294, 290, 265, 25, 4, 0]],
    ["runs 123 and 281, the crashed orphans, failed", "SELECT count(*) FROM runs WHERE id IN (123, 281) AND status = 'failed'", [2]],
    ["attempts, one per run", "SELECT count(*), count(DISTINCT run_id) FROM run_attempts", [294, 294]],
    ["model calls, runs, cents; effort not back-filled", "SELECT count(*), count(DISTINCT run_id), round(sum(api_cost_usd)::numeric * 100), sum((effort IS NULL)::int) FROM model_calls", [2205, 191, 76974, 1744]],
    ["artifacts: all current, 128 selections backfilled", "SELECT count(*), sum((status = 'current')::int), (SELECT count(*) FROM artifacts WHERE name = 'selections.json') FROM artifacts", [2109, 2109, 227]],
    ["artifacts carry their stage from the name", "SELECT sum((stage IS NULL)::int) FROM artifacts", [0]],
    ["issues: all revision 1, five without a run", "SELECT count(*), sum((revision = 1)::int), sum((run_id IS NULL)::int) FROM issues", [282, 282, 5]],
    ["sends: the 100 broadcasts, all sent, no claim recorded, each with its id", "SELECT count(*), sum((status = 'sent')::int), sum((claim_token IS NULL AND claimed_at IS NULL)::int), sum((resend_id IS NULL)::int) FROM sends", [100, 100, 100, 0]],
    ["story sources, runs, and every one searchable", "SELECT count(*), count(DISTINCT run_id), sum((search IS NOT NULL AND search <> ''::tsvector)::int) FROM story_sources", [30063, 277, 30063]],
    ["fetched articles, runs", "SELECT count(*), count(DISTINCT run_id) FROM articles", [136143, 229]],
    ["dedup matches", "SELECT count(*) FROM dedup_matches", [24856]],
    ["source fetches, rows without a run", "SELECT count(*), sum((run_id IS NULL)::int) FROM source_fetches", [9645, 231]],
    ["threads: active, dormant", "SELECT count(*), sum((status = 'active')::int), sum((status = 'dormant')::int) FROM thread_state", [951, 52, 899]],
    ["thread updates: continuations, with content", "SELECT count(*), sum(is_continuation::int), count(content) FROM thread_updates", [1597, 641, 605]],
    ["questions: open, resolved", "SELECT count(*), sum((status = 'open')::int), sum((status = 'resolved')::int) FROM thread_question_state", [3116, 2408, 708]],
    ["every run readers got is published, and every thread update's run with it", "SELECT count(*), (SELECT count(*) FROM thread_updates WHERE run_id NOT IN (SELECT run_id FROM published_runs)) FROM published_runs", [277, 0]],
    ["the next ids follow the imported ones", "SELECT (SELECT last_value FROM runs_id_seq) - (SELECT max(id) FROM runs)", [1]],
  ] as [string, string, number[]][])("%s", async (_name, sql, expected) => {
    expect(await n(sql, expected.length)).toEqual(expected);
  });
});
