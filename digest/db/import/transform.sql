-- The legacy SQLite database, as pgloader loads it into the `legacy` schema (db/import/legacy.load:
-- every table, times as text, reals as double precision), copied into the product schema. The mapping
-- and its expectations are docs/2026-09-23-data-model-design.md §5.1. Legacy times are UTC text.
CREATE OR REPLACE FUNCTION pg_temp.utc(t text) RETURNS timestamptz LANGUAGE sql IMMUTABLE AS $$ SELECT t::timestamp AT TIME ZONE 'UTC' $$;

-- Python marked a run completed whether or not it emailed: a recipient count is the only evidence of a
-- send, so the rest are 'unrecorded'. A run still 'running' is a crash nobody marked.
INSERT INTO digest_runs (id, run_at, articles_kept, articles_emailed, completed_at, git_sha, status, outcome, error)
SELECT id, pg_temp.utc(run_at), articles_kept, articles_emailed, pg_temp.utc(completed_at), git_sha,
       CASE status WHEN 'running' THEN 'failed' ELSE status END,
       CASE WHEN status = 'completed' THEN CASE WHEN articles_emailed > 0 THEN 'sent' ELSE 'unrecorded' END END,
       error
FROM legacy.digest_runs;

-- One attempt per run, numbered as the run: every legacy row was written by that one Python process.
INSERT INTO run_attempts (id, run_id, pipeline, git_sha, started_at, ended_at, state)
SELECT id, id, 'python', git_sha, pg_temp.utc(run_at), pg_temp.utc(completed_at),
       CASE status WHEN 'completed' THEN 'finished' ELSE 'failed' END
FROM legacy.digest_runs;

INSERT INTO run_usage (id, run_id, attempt_id, subagent, model, input_tokens, output_tokens, cache_write_tokens,
                       cache_read_tokens, api_cost_usd, duration_ms, thinking, effort, recorded_at)
SELECT id, run_id, run_id, subagent, model, input_tokens, output_tokens, cache_write_tokens,
       cache_read_tokens, api_cost_usd, duration_ms, thinking, effort, pg_temp.utc(recorded_at)
FROM legacy.run_usage;

-- stage, kind and branch are filled from the names by the importer, with the writer's own function.
INSERT INTO run_artifacts (id, run_id, attempt_id, artifact_name, content, sha256, state, created_at)
SELECT id, run_id, run_id,
       regexp_replace(artifact_name, '\.corrupt\.\d+$', ''),
       content, encode(sha256(convert_to(content, 'UTF8')), 'hex'),
       CASE WHEN artifact_name ~ '\.corrupt\.\d+$' THEN 'quarantined' ELSE 'current' END,
       pg_temp.utc(created_at)
FROM legacy.run_artifacts;
-- Runs whose selections exist only in the retired table, numbered after the legacy ids (an insert with
-- explicit ids does not move the identity).
SELECT setval(pg_get_serial_sequence('run_artifacts', 'id'), (SELECT COALESCE(max(id), 0) + 1 FROM run_artifacts), false);
INSERT INTO run_artifacts (run_id, attempt_id, artifact_name, content, sha256, created_at)
SELECT s.run_id, s.run_id, 'selections.json', s.selections_json, encode(sha256(convert_to(s.selections_json, 'UTF8')), 'hex'), pg_temp.utc(s.created_at)
FROM legacy.selections s
WHERE NOT EXISTS (SELECT 1 FROM legacy.run_artifacts a WHERE a.run_id = s.run_id AND a.artifact_name = 'selections.json');

-- Published when its run completed; digests.created_at is a backfill stamp on 31 rows (2026-01-15), so
-- it stands in only where no run is linked.
INSERT INTO issues (date, revision, run_id, html, preheader, published_at)
SELECT d.date::date, 1, d.run_id, d.html, COALESCE(d.preheader, ''), COALESCE(pg_temp.utc(r.completed_at), pg_temp.utc(d.created_at))
FROM legacy.digests d LEFT JOIN legacy.digest_runs r ON r.id = d.run_id;

-- Every legacy send reached Resend ('sent'); no claim was recorded before this schema.
INSERT INTO broadcasts (date, run_id, resend_id, revision, status, recipients)
SELECT date::date, run_id, broadcast_id, 1, broadcast_status, broadcast_recipients
FROM legacy.digests WHERE broadcast_status IS NOT NULL;

INSERT INTO shown_narratives (id, headline, tier, shown_at, source_id, run_id, original_title, cluster_id)
SELECT id, headline, tier, pg_temp.utc(shown_at), source_id, run_id, original_title, cluster_id
FROM legacy.shown_narratives;

INSERT INTO fetched_articles (id, run_id, source_id, title, url, published, summary, fetched_at)
SELECT id, run_id, source_id, title, url, published, summary, pg_temp.utc(fetched_at)
FROM legacy.fetched_articles;

INSERT INTO source_health (id, source_id, success, error_message, recorded_at, articles_fetched, articles_kept, run_id)
SELECT id, source_id, success <> 0, error_message, pg_temp.utc(recorded_at), articles_fetched, articles_kept, run_id
FROM legacy.source_health;

INSERT INTO dedup_log (id, logged_at, article_title, article_source_id, matched_headline, similarity, threshold, run_id)
SELECT id, pg_temp.utc(logged_at), article_title, article_source_id, matched_headline, similarity, threshold, run_id
FROM legacy.dedup_log;

INSERT INTO threads (id, created_run_id, merged_into, created_at)
SELECT id, first_run_id, NULL, pg_temp.utc(created_at) FROM legacy.threads;
UPDATE threads t SET merged_into = l.merged_into FROM legacy.threads l WHERE l.id = t.id AND l.merged_into IS NOT NULL;

INSERT INTO thread_installments (id, thread_id, run_id, cluster_story, continued, content, created_at)
SELECT id, thread_id, run_id, cluster_story, matched_score IS NOT NULL, content, pg_temp.utc(created_at)
FROM legacy.thread_installments;

INSERT INTO thread_questions (id, thread_id, question, raised_run_id, created_at)
SELECT id, thread_id, question, raised_run_id, pg_temp.utc(created_at) FROM legacy.thread_questions;

-- Resolved when the resolving run ran (the legacy row kept only when the question was raised).
INSERT INTO thread_question_resolutions (question_id, run_id, how, created_at)
SELECT q.id, q.resolved_run_id, COALESCE(q.resolved_how, ''), pg_temp.utc(r.run_at)
FROM legacy.thread_questions q JOIN legacy.digest_runs r ON r.id = q.resolved_run_id WHERE q.status = 'resolved';
