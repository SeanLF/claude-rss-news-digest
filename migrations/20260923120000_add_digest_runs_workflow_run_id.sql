-- The Temporal workflow execution that inserted the row. A retried startRun finds its own row by it
-- instead of inserting a second one or refusing the day as already running. NULL for Python runs,
-- which never read it.
ALTER TABLE digest_runs ADD COLUMN workflow_run_id TEXT;
