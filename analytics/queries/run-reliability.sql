-- QUESTION: How often does a run fail outright, silently skip a stage, or produce a
--   digest nobody received -- and how long do runs take?
-- WHY: A daily unattended pipeline's real reliability is not "did the process exit 0"
--   but "did a correct digest reach subscribers". This checks the four ways that can
--   fail independently: the run record, the per-stage usage record, the archived
--   artifacts, and the broadcast. A run that completed but shipped zero stories, or
--   sent to zero recipients, is a silent outage and is counted here as such.
-- CAVEAT: Missing stage/artifact rows are FAIL-SOFT writes -- absence means "not
--   recorded", which conflates "stage did not run" with "the archive write failed".
--   It is a smoke alarm, not a diagnosis. Runs before 204 legitimately have no
--   artifacts and before 106 no usage rows, so restrict the window or expect
--   false positives. Duration is run_at -> completed_at wall clock, which includes
--   feed fetch and email send, not just model time.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
r AS (
    SELECT
        dr.id, date(dr.run_at) AS run_date, dr.status,
        (julianday(dr.completed_at) - julianday(dr.run_at)) * 1440.0 AS minutes,
        (SELECT COUNT(DISTINCT headline) FROM shown_narratives sn WHERE sn.run_id = dr.id) AS shipped,
        (SELECT COUNT(DISTINCT subagent) FROM run_usage ru WHERE ru.run_id = dr.id)        AS stages,
        (SELECT COUNT(*) FROM run_artifacts ra WHERE ra.run_id = dr.id)                    AS artifacts,
        (SELECT COALESCE(SUM(broadcast_recipients), 0) FROM digests d WHERE d.run_id = dr.id) AS recipients,
        (SELECT COUNT(*) FROM source_health sh WHERE sh.run_id = dr.id AND sh.success = 0)  AS feed_failures,
        substr(COALESCE(dr.error, ''), 1, 50) AS error
    FROM digest_runs dr, bounds b
    WHERE dr.id >= b.lo
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
