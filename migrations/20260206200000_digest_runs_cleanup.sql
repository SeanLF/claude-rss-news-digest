-- Add completion tracking and git SHA to digest_runs

ALTER TABLE digest_runs ADD COLUMN completed_at DATETIME;
ALTER TABLE digest_runs ADD COLUMN git_sha TEXT;

-- Backfill: existing rows with non-null counts were completed
UPDATE digest_runs SET completed_at = run_at WHERE articles_fetched IS NOT NULL;
