-- Backfill run_id on pre-migration rows using timestamp correlation.
-- Strategy: for each NULL-run_id row, find the last completed digest_run
-- on the same day. Multi-run days get assigned to the final run.

-- shown_narratives: match shown_at to run_at by date
UPDATE shown_narratives
SET run_id = (
    SELECT id FROM digest_runs
    WHERE date(digest_runs.run_at) = date(shown_narratives.shown_at)
    ORDER BY digest_runs.completed_at DESC
    LIMIT 1
)
WHERE run_id IS NULL;

-- source_health: match recorded_at to run_at by date
UPDATE source_health
SET run_id = (
    SELECT id FROM digest_runs
    WHERE date(digest_runs.run_at) = date(source_health.recorded_at)
    ORDER BY digest_runs.completed_at DESC
    LIMIT 1
)
WHERE run_id IS NULL;

-- dedup_log: match logged_at to run_at by date
UPDATE dedup_log
SET run_id = (
    SELECT id FROM digest_runs
    WHERE date(digest_runs.run_at) = date(dedup_log.logged_at)
    ORDER BY digest_runs.completed_at DESC
    LIMIT 1
)
WHERE run_id IS NULL;

-- digests: match date directly to run_at date
UPDATE digests
SET run_id = (
    SELECT id FROM digest_runs
    WHERE date(digest_runs.run_at) = digests.date
    ORDER BY digest_runs.completed_at DESC
    LIMIT 1
)
WHERE run_id IS NULL;
