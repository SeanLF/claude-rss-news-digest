import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { PGlite } from "@electric-sql/pglite";
import { beforeAll, describe, expect, it } from "vitest";
import { PARSERS } from "./db.js";
import { dropLegacy, transform, verify } from "./import.js";
import { copyLegacy } from "./legacy-copy.js";
import { pgliteDb } from "./pglite.js";
import { upSections } from "./schema.js";

// A legacy SQLite file in the prod file's declared types (INTEGER, REAL, TEXT, DATETIME for times),
// holding each case §5.1 names, imported the way bin/import-legacy imports the real one: copyLegacy,
// the migrations, transform. The real file is imported and checked by `make import-check`.
const LEGACY = `
CREATE TABLE digest_runs (id INTEGER, run_at DATETIME, articles_kept INTEGER, articles_emailed INTEGER, completed_at DATETIME, git_sha TEXT, status TEXT, error TEXT);
CREATE TABLE run_usage (id INTEGER, run_id INTEGER, subagent TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER, api_cost_usd REAL, recorded_at DATETIME, duration_ms INTEGER, thinking TEXT, effort TEXT);
CREATE TABLE run_artifacts (id INTEGER, run_id INTEGER, artifact_name TEXT, content TEXT, created_at DATETIME);
CREATE TABLE selections (id INTEGER, run_id INTEGER, selections_json TEXT, created_at DATETIME);
CREATE TABLE cluster_runs (id INTEGER, run_id INTEGER, clusters_json TEXT, created_at DATETIME);
CREATE TABLE digests (date TEXT, html TEXT, created_at DATETIME, preheader TEXT, run_id INTEGER, broadcast_id TEXT, broadcast_status TEXT, broadcast_recipients INTEGER);
CREATE TABLE shown_narratives (id INTEGER, headline TEXT, tier TEXT, shown_at DATETIME, source_id TEXT, run_id INTEGER, original_title TEXT, cluster_id TEXT);
CREATE TABLE fetched_articles (id INTEGER, run_id INTEGER, source_id TEXT, title TEXT, url TEXT, published TEXT, summary TEXT, fetched_at DATETIME);
CREATE TABLE source_health (id INTEGER, source_id TEXT, success INTEGER, error_message TEXT, recorded_at DATETIME, articles_fetched INTEGER, articles_kept INTEGER, run_id INTEGER);
CREATE TABLE dedup_log (id INTEGER, logged_at DATETIME, article_title TEXT, article_source_id TEXT, matched_headline TEXT, similarity REAL, threshold REAL, action TEXT, run_id INTEGER);
CREATE TABLE threads (id INTEGER, slug TEXT, label TEXT, status TEXT, first_run_id INTEGER, last_run_id INTEGER, created_at DATETIME, updated_at DATETIME, merged_into INTEGER);
CREATE TABLE thread_installments (id INTEGER, thread_id INTEGER, run_id INTEGER, cluster_story TEXT, matched_score REAL, created_at DATETIME, content TEXT);
CREATE TABLE thread_questions (id INTEGER, thread_id INTEGER, question TEXT, status TEXT, raised_run_id INTEGER, resolved_run_id INTEGER, resolved_how TEXT, created_at DATETIME);
CREATE TABLE thread_runs (id INTEGER, run_id INTEGER, threads_synthesized INTEGER, audit_failures INTEGER, created_at DATETIME);
CREATE TABLE story_feedback (id INTEGER, digest_date TEXT, story TEXT, vote TEXT, created_at DATETIME);

-- 1: sent. 2: completed, emailed nobody, its issue overwritten by 3 the same day. 3: sent. 4: a crash
-- left running. 5: failed. 6: the newest run, before any installment of thread 2 went dormant.
INSERT INTO digest_runs VALUES
  (1, '2026-09-10 10:25:00', 40, 12, '2026-09-10 10:45:00', 'a', 'completed', NULL),
  (2, '2026-09-11 10:25:00', 30, 0, '2026-09-11 10:40:00', 'b', 'completed', NULL),
  (3, '2026-09-11 14:00:00', 31, 12, '2026-09-11 14:20:00', 'b', 'completed', NULL),
  (4, '2026-09-12 10:25:00', NULL, 0, NULL, 'c', 'running', NULL),
  (5, '2026-09-13 10:25:00', NULL, 0, NULL, 'c', 'failed', 'boom'),
  (6, '2026-09-14 10:25:00', 20, 11, '2026-09-14 10:50:00', 'd', 'completed', NULL);
INSERT INTO run_usage VALUES (1, 1, 'write', 'm', 10, 5, 0, 7, 0.25, '2026-09-10 10:30:00', 900, 'adaptive', NULL), (2, 3, 'select', 'm', 1, 1, 0, 0, 0.125, '2026-09-11 14:05:00', 50, 'disabled', '(sdk default)');
INSERT INTO run_artifacts VALUES
  (1, 3, 'selections.json', '{"must_know":[]}', '2026-09-11 14:10:00'),
  (2, 3, 'draft_s01.json', '{"x":1}', '2026-09-11 14:08:00'),
  (3, 3, 'recap.txt.corrupt.1', 'bad', '2026-09-11 14:02:00'),
  (4, 3, 'recap.txt', 'good', '2026-09-11 14:03:00');
INSERT INTO selections VALUES (1, 1, '{"must_know":["run 1"]}', '2026-09-10 10:44:00'), (2, 3, '{"must_know":[]}', '2026-09-11 14:10:00');
INSERT INTO digests VALUES
  ('2025-12-26', '<p>legacy</p>', '2026-01-15 13:21:26', '', NULL, NULL, NULL, NULL),
  ('2026-09-10', '<p>one</p>', '2026-09-10 10:44:00', 'pre', 1, 'b1', 'sent', 12),
  ('2026-09-11', '<p>three</p>', '2026-09-11 14:19:00', 'pre3', 3, NULL, NULL, NULL),
  ('2026-09-14', '<p>six</p>', '2026-09-14 10:49:00', NULL, 6, 'b6', 'sent', 11);
INSERT INTO shown_narratives VALUES (1, 'Deal signed', 'must_know', '2026-09-10 10:44:00', 'reuters', 1, 'Deal is signed', 'c1');
INSERT INTO fetched_articles VALUES (1, 1, 'reuters', 'T', 'https://r.test/a', 'Thu, 10 Sep 2026 08:00:00 GMT', 'S', '2026-09-10 10:26:00');
INSERT INTO source_health VALUES (1, 'reuters', 1, NULL, '2026-09-10 10:26:00', 40, 30, 1), (2, 'old', 0, 'x', '2025-12-01 10:00:00', 0, 0, NULL);
INSERT INTO dedup_log VALUES (1, '2026-09-10 10:27:00', 'T', 'reuters', 'Deal', 0.5242494216998329, 0.8, 'filtered', 1);
INSERT INTO threads VALUES (1, 'deal', 'Deal day two', 'active', 1, 6, '2026-09-10 10:44:00', '2026-09-14 10:49:00', NULL), (2, 'yen', 'Yen', 'active', 1, 1, '2026-09-10 10:44:00', '2026-09-10 10:44:00', NULL);
INSERT INTO thread_installments VALUES
  (1, 1, 1, 'Deal signed', NULL, '2026-09-10 10:44:00', NULL),
  (2, 1, 6, 'Deal day two', 1.0, '2026-09-14 10:49:00', '{"whats_new":[]}'),
  (3, 2, 1, 'Yen', NULL, '2026-09-10 10:44:00', NULL);
INSERT INTO thread_questions VALUES (1, 1, 'Will it hold?', 'resolved', 1, 6, 'It held.', '2026-09-10 10:44:00'), (2, 1, 'Who pays?', 'open', 6, NULL, NULL, '2026-09-14 10:49:00');
`;

// One instance for the file, rebuilt per test: a PGlite instance costs ~250 MiB that closing it does
// not give back (measured), and vitest runs each file in its own worker.
let instance: PGlite | undefined;
let legacyPath: string | undefined;
function sqliteFile(sql: string): string {
  const path = join(mkdtempSync(join(tmpdir(), "legacy-")), "digest.db");
  const db = new DatabaseSync(path);
  db.exec(sql);
  db.close();
  return path;
}
async function imported(): Promise<PGlite> {
  const pg = (instance ??= new PGlite({ parsers: PARSERS }));
  await pg.exec("SET TIME ZONE 'UTC'; DROP SCHEMA IF EXISTS legacy CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public");
  const db = pgliteDb(pg);
  await copyLegacy(db, (legacyPath ??= sqliteFile(LEGACY)));
  for (const up of upSections()) await pg.exec(up);
  await transform(db);
  return pg;
}
// Started before the first test, whose 5 s would otherwise include PGlite's start on a loaded host.
beforeAll(async () => {
  await (instance ??= new PGlite({ parsers: PARSERS })).query("SELECT 1");
}, 60_000);
const broken = async (pg: PGlite) => (await verify(pgliteDb(pg))).filter((c) => c.broken !== 0).map((c) => c.name);

describe("the legacy import (transform and verify)", () => {
  it("copies every case §5.1 names, and every check holds", async () => {
    const pg = await imported();
    const q = async (sql: string) => (await pg.query(sql)).rows;
    expect(await broken(pg)).toEqual([]);
    expect(await q("SELECT r.id, r.status, r.outcome, a.status AS attempt, a.ended_at FROM runs r JOIN run_attempts a ON a.run_id = r.id ORDER BY r.id")).toEqual([
      { id: 1, status: "completed", outcome: "sent", attempt: "completed", ended_at: "2026-09-10 10:45:00" },
      { id: 2, status: "completed", outcome: "unrecorded", attempt: "completed", ended_at: "2026-09-11 10:40:00" },
      { id: 3, status: "completed", outcome: "sent", attempt: "completed", ended_at: "2026-09-11 14:20:00" },
      { id: 4, status: "failed", outcome: null, attempt: "failed", ended_at: null },
      { id: 5, status: "failed", outcome: null, attempt: "failed", ended_at: null },
      { id: 6, status: "completed", outcome: "sent", attempt: "completed", ended_at: "2026-09-14 10:50:00" },
    ]);
    expect(await q("SELECT run_id, name, status, stage, kind, branch FROM artifacts ORDER BY run_id, id")).toEqual([
      { run_id: 1, name: "selections.json", status: "current", stage: "assemble", kind: "output", branch: null },
      { run_id: 3, name: "selections.json", status: "current", stage: "assemble", kind: "output", branch: null },
      { run_id: 3, name: "draft_s01.json", status: "current", stage: "write", kind: "output", branch: "s01" },
      { run_id: 3, name: "recap.txt", status: "quarantined", stage: "recap", kind: "output", branch: null },
      { run_id: 3, name: "recap.txt", status: "current", stage: "recap", kind: "output", branch: null },
    ]);
    expect(await q("SELECT issue_date, revision, run_id, preheader FROM issues ORDER BY issue_date")).toEqual([
      { issue_date: "2025-12-26", revision: 1, run_id: null, preheader: "" },
      { issue_date: "2026-09-10", revision: 1, run_id: 1, preheader: "pre" },
      { issue_date: "2026-09-11", revision: 1, run_id: 3, preheader: "pre3" },
      { issue_date: "2026-09-14", revision: 1, run_id: 6, preheader: "" },
    ]);
    // Run 3 emailed 12 readers before broadcasts existed, as Resend transactional emails whose counts
    // live in Resend: its run is sent, and it has no send.
    expect(await q("SELECT issue_date, run_id, resend_id, status, recipients, claim_token FROM sends ORDER BY issue_date")).toEqual([
      { issue_date: "2026-09-10", run_id: 1, resend_id: "b1", status: "sent", recipients: 12, claim_token: null },
      { issue_date: "2026-09-14", run_id: 6, resend_id: "b6", status: "sent", recipients: 11, claim_token: null },
    ]);
    expect(await q("SELECT is_success, error, run_id FROM source_fetches ORDER BY id")).toEqual([{ is_success: true, error: null, run_id: 1 }, { is_success: false, error: "x", run_id: null }]);
    expect(await q("SELECT title, source_id, similarity FROM dedup_matches")).toEqual([{ title: "T", source_id: "reuters", similarity: 0.5242494216998329 }]);
    expect(await q("SELECT published_raw FROM articles")).toEqual([{ published_raw: "Thu, 10 Sep 2026 08:00:00 GMT" }]);
    expect(await q("SELECT stage, request_model, cache_creation_input_tokens, cache_read_input_tokens FROM model_calls ORDER BY id")).toEqual([
      { stage: "write", request_model: "m", cache_creation_input_tokens: 0, cache_read_input_tokens: 7 },
      { stage: "select", request_model: "m", cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
    ]);
    expect(await q("SELECT id, label, status FROM thread_state ORDER BY id")).toEqual([{ id: 1, label: "Deal day two", status: "active" }, { id: 2, label: "Yen", status: "active" }]);
    expect(await q("SELECT id, is_continuation FROM thread_updates ORDER BY id")).toEqual([{ id: 1, is_continuation: false }, { id: 2, is_continuation: true }, { id: 3, is_continuation: false }]);
    expect(await q("SELECT id, status, resolved_run_id, answer FROM thread_question_state ORDER BY id")).toEqual([
      { id: 1, status: "resolved", resolved_run_id: 6, answer: "It held." },
      { id: 2, status: "open", resolved_run_id: null, answer: null },
    ]);
    expect(await q("SELECT source_title, search @@ websearch_to_tsquery('english', 'deal') AS hit FROM story_sources")).toEqual([{ source_title: "Deal is signed", hit: true }]);
    // The identities continue after the imported ids.
    await pg.exec("INSERT INTO runs DEFAULT VALUES");
    expect(await q("SELECT max(id) AS id FROM runs")).toEqual([{ id: 7 }]);
    await dropLegacy(pgliteDb(pg));
    expect(await q("SELECT count(*) AS n FROM pg_namespace WHERE nspname = 'legacy'")).toEqual([{ n: 0 }]);
  });

  // Negative controls: each check must see its own kind of loss.
  it.each([
    ["every issue, html byte for byte", "UPDATE issues SET html = html || ' ' WHERE issue_date = '2026-09-10'"],
    ["every thread's derived label is its stored label", "UPDATE thread_updates SET label = 'x' WHERE id = 2"],
    ["every thread's derived status is its stored status", "UPDATE legacy.threads SET status = 'dormant' WHERE id = 2"],
    ["every artifact, content byte for byte", "UPDATE artifacts SET content = 'y' WHERE id = 2"],
    ["a sent outcome only where the legacy run emailed someone", "ALTER TABLE runs DISABLE TRIGGER runs_transition; UPDATE runs SET outcome = 'sent' WHERE id = 2"],
    ["every send, with its broadcast id and recipients", "UPDATE sends SET recipients = 13 WHERE issue_date = '2026-09-10'"],
    ["every send, with its broadcast id and recipients", "DELETE FROM sends WHERE issue_date = '2026-09-10'"],
    ["every send, with its broadcast id and recipients", "INSERT INTO sends (issue_date, run_id, revision, status, recipients) VALUES ('2026-09-11', 3, 1, 'sent', 12)"],
    ["every send, with its broadcast id and recipients", "UPDATE sends SET status = 'failed' WHERE issue_date = '2026-09-14'"],
    ["every question, open or resolved as it was", "DELETE FROM thread_question_resolutions"],
    ["every question, open or resolved as it was", "UPDATE thread_question_resolutions SET answer = ''"],
    ["a selections.json for every run the retired table held", "DELETE FROM artifacts WHERE run_id = 1"],
    ["every legacy run is a run, with its times, counts and error", "UPDATE runs SET started_at = started_at + interval '1 hour' WHERE id = 5"],
    ["every legacy run is a run, with its times, counts and error", "UPDATE run_attempts SET ended_at = NULL WHERE id = 1"],
    ["every model call, column for column", "UPDATE model_calls SET input_tokens = output_tokens, output_tokens = input_tokens WHERE id = 1"],
    ["no issue the legacy file did not have", "INSERT INTO issues (issue_date, revision, html) VALUES ('2026-09-10', 2, '')"],
    ["every shown story source, column for column", "UPDATE story_sources SET tier = 'should_know'"],
    ["every fetched article, column for column", "UPDATE articles SET published_raw = NULL"],
    ["every source fetch, column for column", "UPDATE source_fetches SET fetched_at = fetched_at + interval '1 second' WHERE id = 2"],
    ["every dedup match, column for column", "UPDATE dedup_matches SET similarity = 0.52"],
    ["every thread update, a continuation as the linker decided", "UPDATE thread_updates SET run_id = 3 WHERE id = 2"],
  ])("%s: fails when the copy loses it (%s)", async (name, loss) => {
    const pg = await imported();
    expect(await broken(pg)).toEqual([]);
    await pg.exec(loss);
    expect(await broken(pg)).toContain(name);
  });
});
