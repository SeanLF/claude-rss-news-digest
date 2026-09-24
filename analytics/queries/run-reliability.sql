-- QUESTION: How often does a run fail outright, silently skip a stage, or produce a
--   digest nobody received -- and how long do runs take?
-- WHY: A daily unattended pipeline's real reliability is not "did the process exit 0"
--   but "did a correct digest reach subscribers". This checks the four ways that can
--   fail independently: the run record, the per-stage usage record, the archived
--   artifacts, and the send. A run that completed but shipped zero stories, or
--   sent to zero recipients, is a silent outage and is counted here as such.
--   `recipients` sums the run's sends Resend holds (queued, sending, sent); a failed or
--   unclaimed send counts zero. A run with outcome 'sent' and no send of its own reads blank,
--   not zero, and is not flagged ZERO_RECIPIENTS: a run mailed before Resend broadcasts (those
--   counts live in Resend, not in this database), or a re-run of a day another run's send
--   already delivered.
-- CAVEAT: Missing stage/artifact rows are FAIL-SOFT writes -- absence means "not
--   recorded", which conflates "stage did not run" with "the archive write failed".
--   It is a smoke alarm, not a diagnosis. Runs before 204 have only the selections.json
--   the import carried (67-200), and before 106 no usage rows, so restrict the window or
--   expect false positives. Duration is started_at -> the last attempt's ended_at, wall
--   clock, which includes feed fetch and email send, not just model time. Imported runs
--   still `running` were crashes and read `failed`.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM runs
),
r AS (
    SELECT
        ru.id, (ru.started_at AT TIME ZONE 'UTC')::date AS run_date, ru.status,
        EXTRACT(EPOCH FROM (SELECT MAX(ended_at) FROM run_attempts a WHERE a.run_id = ru.id)
                           - ru.started_at) / 60.0 AS minutes,
        (SELECT COUNT(DISTINCT headline) FROM story_sources ss WHERE ss.run_id = ru.id) AS shipped,
        (SELECT COUNT(DISTINCT stage) FROM model_calls mc WHERE mc.run_id = ru.id)      AS stages,
        (SELECT COUNT(*) FROM artifacts a WHERE a.run_id = ru.id)                       AS artifacts,
        CASE WHEN ru.outcome = 'sent' AND NOT EXISTS (SELECT 1 FROM sends s WHERE s.run_id = ru.id) THEN NULL
             ELSE (SELECT COALESCE(SUM(recipients), 0) FROM sends s WHERE s.run_id = ru.id AND s.status IN ('queued', 'sending', 'sent'))
        END AS recipients,
        (SELECT COUNT(*) FROM source_fetches sf WHERE sf.run_id = ru.id AND NOT sf.is_success) AS feed_failures,
        substr(COALESCE(ru.error, ''), 1, 50) AS error
    FROM runs ru, bounds b
    WHERE ru.id >= b.lo
)
SELECT
    id AS run_id, run_date, status,
    ROUND(minutes, 1) AS minutes,
    shipped, stages, artifacts, recipients, feed_failures,
    TRIM(
        CASE WHEN status <> 'completed'   THEN 'RUN_NOT_COMPLETED '  ELSE '' END ||
        CASE WHEN shipped = 0             THEN 'ZERO_STORIES '       ELSE '' END ||
        CASE WHEN recipients = 0          THEN 'ZERO_RECIPIENTS '    ELSE '' END ||
        CASE WHEN stages = 0              THEN 'NO_USAGE_RECORDED '  ELSE '' END ||
        CASE WHEN artifacts = 0           THEN 'NO_ARTIFACTS '       ELSE '' END ||
        CASE WHEN feed_failures > 0       THEN 'FEED_ERRORS '        ELSE '' END
    ) AS flags,
    error
FROM r
ORDER BY id DESC;
