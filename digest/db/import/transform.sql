-- The legacy SQLite database, as the copier loads it into the `legacy` schema (store/legacy-copy.ts:
-- every table under its SQLite names, times as text, reals as double precision), copied into the
-- product schema. The mapping and its expectations are docs/2026-09-23-data-model-design.md §5.1.
-- Legacy times are UTC text.
CREATE OR REPLACE FUNCTION pg_temp.utc(t text) RETURNS timestamptz LANGUAGE sql IMMUTABLE AS $$ SELECT t::timestamp AT TIME ZONE 'UTC' $$;

-- Python marked a run completed whether or not it emailed: a recipient count is the only evidence of a
-- send, so the rest are 'unrecorded'. A run still 'running' is a crash nobody marked.
INSERT INTO runs (id, started_at, articles_kept, git_sha, status, outcome, error)
SELECT id, pg_temp.utc(run_at), articles_kept, git_sha,
       CASE status WHEN 'running' THEN 'failed' ELSE status END,
       CASE WHEN status = 'completed' THEN CASE WHEN articles_emailed > 0 THEN 'sent' ELSE 'unrecorded' END END,
       error
FROM legacy.digest_runs;

-- One attempt per run, numbered as the run: every legacy row was written by that one Python process.
-- The run's completed_at is when that process finished.
INSERT INTO run_attempts (id, run_id, pipeline, git_sha, started_at, ended_at, status)
SELECT id, id, 'python', git_sha, pg_temp.utc(run_at), pg_temp.utc(completed_at),
       CASE status WHEN 'completed' THEN 'completed' ELSE 'failed' END
FROM legacy.digest_runs;

INSERT INTO model_calls (id, run_id, attempt_id, stage, request_model, input_tokens, output_tokens, cache_creation_input_tokens,
                         cache_read_input_tokens, api_cost_usd, duration_ms, thinking, effort, recorded_at)
SELECT id, run_id, run_id, subagent, model, input_tokens, output_tokens, cache_write_tokens,
       cache_read_tokens, api_cost_usd, duration_ms, thinking, effort, pg_temp.utc(recorded_at)
FROM legacy.run_usage;

-- stage, kind and branch are filled from the names by the importer, with the writer's own function.
INSERT INTO artifacts (id, run_id, attempt_id, name, content, sha256, status, created_at)
SELECT id, run_id, run_id,
       regexp_replace(artifact_name, '\.corrupt\.\d+$', ''),
       content, encode(sha256(convert_to(content, 'UTF8')), 'hex'),
       CASE WHEN artifact_name ~ '\.corrupt\.\d+$' THEN 'quarantined' ELSE 'current' END,
       pg_temp.utc(created_at)
FROM legacy.run_artifacts;
-- Runs whose selections exist only in the retired table, numbered after the legacy ids (an insert with
-- explicit ids does not move the identity).
SELECT setval(pg_get_serial_sequence('artifacts', 'id'), (SELECT COALESCE(max(id), 0) + 1 FROM artifacts), false);
INSERT INTO artifacts (run_id, attempt_id, name, content, sha256, created_at)
SELECT s.run_id, s.run_id, 'selections.json', s.selections_json, encode(sha256(convert_to(s.selections_json, 'UTF8')), 'hex'), pg_temp.utc(s.created_at)
FROM legacy.selections s
WHERE NOT EXISTS (SELECT 1 FROM legacy.run_artifacts a WHERE a.run_id = s.run_id AND a.artifact_name = 'selections.json');

-- Published when its run completed; digests.created_at is a backfill stamp on 31 rows (2026-01-15), so
-- it stands in only where no run is linked.
INSERT INTO issues (issue_date, revision, run_id, html, preheader, published_at)
SELECT d.date::date, 1, d.run_id, d.html, COALESCE(d.preheader, ''), COALESCE(pg_temp.utc(r.completed_at), pg_temp.utc(d.created_at))
FROM legacy.digests d LEFT JOIN legacy.digest_runs r ON r.id = d.run_id;

-- Every legacy send reached its readers ('sent'); no claim was recorded before this schema. A day
-- mailed before Resend broadcasts has no broadcast id: its recipient count is the run's
-- articles_emailed (which equals the broadcast's recipients on all 100 broadcast days).
INSERT INTO sends (issue_date, run_id, resend_id, revision, status, recipients)
SELECT d.date::date, d.run_id, d.broadcast_id, 1, COALESCE(d.broadcast_status, 'sent'), COALESCE(d.broadcast_recipients, r.articles_emailed)
FROM legacy.digests d LEFT JOIN legacy.digest_runs r ON r.id = d.run_id
WHERE d.broadcast_status IS NOT NULL OR r.articles_emailed > 0;

INSERT INTO story_sources (id, run_id, headline, tier, source_id, source_title, cluster_id, shown_at)
SELECT id, run_id, headline, tier, source_id, original_title, cluster_id, pg_temp.utc(shown_at)
FROM legacy.shown_narratives;

INSERT INTO articles (id, run_id, source_id, title, url, published_raw, summary, fetched_at)
SELECT id, run_id, source_id, title, url, published, summary, pg_temp.utc(fetched_at)
FROM legacy.fetched_articles;

INSERT INTO source_fetches (id, run_id, source_id, is_success, error, articles_fetched, articles_kept, fetched_at)
SELECT id, run_id, source_id, success <> 0, error_message, articles_fetched, articles_kept, pg_temp.utc(recorded_at)
FROM legacy.source_health;

INSERT INTO dedup_matches (id, run_id, title, source_id, matched_headline, similarity, threshold, matched_at)
SELECT id, run_id, article_title, article_source_id, matched_headline, similarity, threshold, pg_temp.utc(logged_at)
FROM legacy.dedup_log;

INSERT INTO threads (id, created_run_id, merged_into_id, created_at)
SELECT id, first_run_id, NULL, pg_temp.utc(created_at) FROM legacy.threads;
UPDATE threads t SET merged_into_id = l.merged_into FROM legacy.threads l WHERE l.id = t.id AND l.merged_into IS NOT NULL;

INSERT INTO thread_updates (id, thread_id, run_id, label, is_continuation, content, created_at)
SELECT id, thread_id, run_id, cluster_story, matched_score IS NOT NULL, content, pg_temp.utc(created_at)
FROM legacy.thread_installments;

INSERT INTO thread_questions (id, thread_id, question, raised_run_id, created_at)
SELECT id, thread_id, question, raised_run_id, pg_temp.utc(created_at) FROM legacy.thread_questions;

-- Resolved when the resolving run ran (the legacy row kept only when the question was raised).
INSERT INTO thread_question_resolutions (question_id, resolved_run_id, answer, created_at)
SELECT q.id, q.resolved_run_id, COALESCE(q.resolved_how, ''), pg_temp.utc(r.run_at)
FROM legacy.thread_questions q JOIN legacy.digest_runs r ON r.id = q.resolved_run_id WHERE q.status = 'resolved';
