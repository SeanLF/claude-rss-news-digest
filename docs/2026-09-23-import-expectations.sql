-- What bin/import-legacy must carry, read from the legacy database (docs/2026-09-23-data-model-design.md §5.1).
-- usage: sqlite3 -readonly data/prod-20260923b.db < docs/2026-09-23-import-expectations.sql
.mode list
.separator " | "
SELECT 'digest_runs: rows, completed, failed, running, with completed_at', count(*), sum(status='completed'), sum(status='failed'), sum(status='running'), sum(completed_at IS NOT NULL) FROM digest_runs;
SELECT 'digest_runs: completed without completed_at, or the reverse', sum(status='completed' AND completed_at IS NULL), sum(status<>'completed' AND completed_at IS NOT NULL) FROM digest_runs;
SELECT 'completed runs: emailed (outcome sent), not emailed with an issue, not emailed without one', sum(r.articles_emailed > 0), sum(r.articles_emailed = 0 AND d.date IS NOT NULL), sum(r.articles_emailed = 0 AND d.date IS NULL) FROM digest_runs r LEFT JOIN digests d ON d.run_id = r.id WHERE r.status = 'completed';
SELECT 'completed runs with a NULL articles_emailed', count(*) FROM digest_runs WHERE status = 'completed' AND articles_emailed IS NULL;
SELECT 'email.html artifacts', count(*) FROM run_artifacts WHERE artifact_name = 'email.html';
SELECT 'digests: created_at NULL', count(*) FROM digests WHERE created_at IS NULL;
SELECT 'threads: updated_at equals the latest installment''s created_at', count(*), sum(t.updated_at = (SELECT created_at FROM thread_installments i WHERE i.thread_id = t.id ORDER BY run_id DESC, id DESC LIMIT 1)) FROM threads t;
SELECT 'run_usage: rows, runs, cost, effort NULL', count(*), count(DISTINCT run_id), round(sum(api_cost_usd), 4), sum(effort IS NULL) FROM run_usage;
SELECT 'run_artifacts: rows, runs, bytes', count(*), count(DISTINCT run_id), sum(length(content)) FROM run_artifacts;
SELECT 'selections: rows, runs', count(*), count(DISTINCT run_id) FROM selections;
SELECT 'selections: only in the table (backfilled as artifacts)', count(*) FROM selections s WHERE NOT EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = s.run_id AND a.artifact_name = 'selections.json');
SELECT 'selections: equal to, differing from, the artifact', sum(a.content = s.selections_json), sum(a.content <> s.selections_json) FROM selections s JOIN run_artifacts a ON a.run_id = s.run_id AND a.artifact_name = 'selections.json';
SELECT 'cluster_runs: rows, without an artifact, differing', count(*), sum(NOT EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = c.run_id AND a.artifact_name = 'clusters.json')), sum(EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = c.run_id AND a.artifact_name = 'clusters.json' AND a.content <> c.clusters_json)) FROM cluster_runs c;
SELECT 'digests: rows, run_id NULL, sent, with a broadcast id, html bytes', count(*), sum(run_id IS NULL), sum(broadcast_status = 'sent'), sum(broadcast_id IS NOT NULL), sum(length(html)) FROM digests;
SELECT 'digests: a send state other than sent', count(*) FROM digests WHERE broadcast_status IS NOT NULL AND broadcast_status <> 'sent';
SELECT 'shown_narratives: rows, runs; fts rows', count(*), count(DISTINCT run_id), (SELECT count(*) FROM shown_narratives_fts) FROM shown_narratives;
SELECT 'fetched_articles: rows, runs', count(*), count(DISTINCT run_id) FROM fetched_articles;
SELECT 'dedup_log: rows, action filtered', count(*), sum(action = 'filtered') FROM dedup_log;
SELECT 'source_health: rows, run_id NULL', count(*), sum(run_id IS NULL) FROM source_health;
SELECT 'threads: rows, active, dormant, merged', count(*), sum(status = 'active'), sum(status = 'dormant'), count(merged_into) FROM threads;
SELECT 'thread_installments: rows, continued, with content', count(*), sum(matched_score IS NOT NULL), count(content) FROM thread_installments;
SELECT 'thread_questions: rows, open, resolved', count(*), sum(status = 'open'), sum(status = 'resolved') FROM thread_questions;
SELECT 'thread_runs, story_feedback (not carried)', (SELECT count(*) FROM thread_runs), (SELECT count(*) FROM story_feedback);
SELECT 'threads: label equals the latest installment', count(*), sum(t.label = (SELECT cluster_story FROM thread_installments i WHERE i.thread_id = t.id ORDER BY run_id DESC, id DESC LIMIT 1)) FROM threads t;
SELECT 'threads: status equals dormancy counted before the newest run', count(*), sum(t.status = CASE WHEN (SELECT count(*) FROM digest_runs r WHERE r.id > t.last_run_id AND r.id < (SELECT max(id) FROM digest_runs) AND r.completed_at IS NOT NULL) > 3 THEN 'dormant' ELSE 'active' END) FROM threads t;
SELECT 'threads: status equals dormancy counted over every run', count(*), sum(t.status = CASE WHEN (SELECT count(*) FROM digest_runs r WHERE r.id > t.last_run_id AND r.completed_at IS NOT NULL) > 3 THEN 'dormant' ELSE 'active' END) FROM threads t;
SELECT 'thread_installments: run not published', count(*) FROM thread_installments WHERE run_id NOT IN (SELECT run_id FROM digests WHERE run_id IS NOT NULL);
SELECT 'yoyo head', max(migration_id) FROM _yoyo_migration;
