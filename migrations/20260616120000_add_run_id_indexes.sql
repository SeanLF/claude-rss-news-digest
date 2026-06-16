-- Index the run_id foreign keys that lacked one, plus shown_at for recency scans.
--
-- delete_run() (and the failed-run rollback) deletes per-run from every child
-- table, and reproduction/usage queries filter by run_id. Without these indexes
-- those operations full-scan the table -- notably fetched_articles (70k+ rows).
-- The dedup recency query scans shown_narratives by shown_at every digest.
--
-- All additive and backward-safe (CREATE INDEX IF NOT EXISTS). cluster_runs,
-- run_artifacts and dedup_log already have run_id indexes, so they are omitted.

CREATE INDEX IF NOT EXISTS idx_fetched_articles_run_id ON fetched_articles(run_id);
CREATE INDEX IF NOT EXISTS idx_shown_narratives_run_id ON shown_narratives(run_id);
CREATE INDEX IF NOT EXISTS idx_run_usage_run_id ON run_usage(run_id);
CREATE INDEX IF NOT EXISTS idx_source_health_run_id ON source_health(run_id);
CREATE INDEX IF NOT EXISTS idx_selections_run_id ON selections(run_id);

CREATE INDEX IF NOT EXISTS idx_shown_narratives_shown_at ON shown_narratives(shown_at);
