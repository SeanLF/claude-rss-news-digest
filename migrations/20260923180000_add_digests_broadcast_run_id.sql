-- The run whose send attempt took the day's claim: digests.run_id names the last run to SAVE the
-- row, which a forced re-run of a sent day takes over. The thread cleanup reads this to tell a
-- failed run that sent from one that did not. NULL for rows claimed before it, and for Python runs,
-- which never read it.
ALTER TABLE digests ADD COLUMN broadcast_run_id INTEGER;
