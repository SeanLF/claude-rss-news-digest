-- One artifact per (run, name).
--
-- Nothing enforced this, so a second write under the same name simply added a row.
-- get_run_artifacts() collapses the table to {artifact_name: content}, so one of the
-- two would win arbitrarily and bin/trace would show only that one -- a silent fork
-- in the record a run is reproduced from. Nothing produces duplicates today
-- (--resume allocates a fresh run_id), but the reason is incidental rather than
-- structural, and run_health now reads an invariant out of one of these blobs.
--
-- The local clone shows 0 duplicate (run_id, artifact_name) groups across all 495 rows
-- through run 247, but it is a stale copy of prod, and the failure mode if prod differs
-- is not a failed migration -- it is a permanent outage. db.init() applies pending
-- migrations at the top of EVERY mode, the IntegrityError is not caught, and IF NOT
-- EXISTS does not help because the failure is the constraint rather than the object. One
-- duplicate row would break every subsequent run including --resume, and the repair would
-- have to be done by hand inside a Docker volume on a box with no sqlite3 binary. So the
-- migration makes itself applicable rather than assuming it is.
--
-- The run_id IS NOT NULL filter is load-bearing and is NOT symmetric with the index:
-- SQLite treats NULLs as distinct in a unique index, so NULL-run_id rows are never
-- constrained, while GROUP BY collapses them into a single group -- an unfiltered dedupe
-- would delete rows the index would have kept. Nothing writes a NULL run_id today (both
-- call sites early-return when _state.run_id is None), so the index simply does not
-- constrain a row shape that does not occur.
DELETE FROM run_artifacts
 WHERE run_id IS NOT NULL
   AND id NOT IN (
       SELECT MAX(id) FROM run_artifacts WHERE run_id IS NOT NULL GROUP BY run_id, artifact_name
   );

-- Keeps the newest row per (run, name), matching the INSERT OR REPLACE in db.py.
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_artifacts_run_name ON run_artifacts(run_id, artifact_name);
