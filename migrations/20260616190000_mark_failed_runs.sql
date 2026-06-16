-- Mark failed runs instead of deleting them.
--
-- abort_run() previously called delete_run(), wiping the run and every child row
-- on any failure -- including failures AFTER the digest was already emailed (a
-- read-timeout on the Resend send response on 2026-06-16 deleted a delivered
-- digest's run record and all its shown_narratives). That destroyed the only
-- record of a delivered digest and left the incident undiagnosable from the DB.
--
-- We now mark a run 'failed' and keep its rows for forensics. Dedup stays correct
-- because shown_narratives is only written on successful delivery, so a failed
-- run contributes no dedup data. The duplicate-run guard keys on completed_at
-- (NULL for a failed run), so keeping failed rows cannot cause a double-send.
--
-- status: 'running' (default, at start_run) -> 'completed' | 'failed'.
-- Additive and backward-safe; existing finished rows are backfilled to completed.

ALTER TABLE digest_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'running';
ALTER TABLE digest_runs ADD COLUMN error TEXT;

UPDATE digest_runs SET status = 'completed' WHERE completed_at IS NOT NULL;
