-- QUESTION: How many written stories fail the coherence fact-check, and since repair
--   was enabled, how many of those are rescued instead of dropped?
-- WHY: Repair-not-drop shipped to production at run 241 with an offline eval claiming
--   it rescues ~4 of every 5 would-be-dropped stories. This is the production
--   read-out of that claim, and it is the query to run each morning during the watch
--   period: `failed` is the exposure, `lost` is what the reader never saw. The
--   pre-241 rows are the built-in baseline -- without them a run showing 3 failures
--   and 0 losses looks like a broken checker rather than a working repair.
-- CAVEAT: `lost` is inferred as (checked - shipped), not directly recorded. Any other
--   reason a written story fails to ship (render guard, dedup at assembly, a manual
--   pull) is attributed to coherence here. The coherence_report.json artifact is the
--   final one persisted for the run, so on repair-enabled runs it may already reflect
--   a post-repair re-check rather than the original verdict -- meaning `failed` after
--   run 241 is not strictly comparable to `failed` before it. Only runs 204+ have the
--   artifact at all. `failed_but_shipped` is NOT the same as "repair rescued it": it fires
--   on repair-OFF runs too (240, 238, 237 all show it with zero repair calls), because
--   merge.py's field-aware degradation strips only why_it_matters and keeps the story. So
--   this column conflates repair rewriting content with a field being blanked. Read it
--   against `repair_calls`, and do not cite it alone as evidence repair works.
--   `failed_but_shipped` and `unexplained_loss` are clamped at zero because the
--   two are inferred from the same subtraction and cannot both be positive; a
--   non-zero `unexplained_loss` means a story vanished for a reason this table cannot
--   see, and is a prompt to go read the run's trace, not a coherence statistic.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
coh AS (
    SELECT ra.run_id,
           COUNT(*) AS checked,
           SUM(json_extract(j.value, '$.pass') IN (0, 'false')) AS failed
    FROM run_artifacts ra, json_each(ra.content, '$.results') j, bounds b
    WHERE ra.artifact_name = 'coherence_report.json' AND ra.run_id >= b.lo
    GROUP BY ra.run_id
),
ship AS (
    SELECT run_id, COUNT(DISTINCT headline) AS shipped
    FROM shown_narratives, bounds WHERE run_id >= bounds.lo GROUP BY run_id
),
rep AS (
    SELECT run_id, COUNT(*) AS repair_calls
    FROM run_usage, bounds
    WHERE run_id >= bounds.lo AND subagent LIKE 'repair%' GROUP BY run_id
)
SELECT
    c.run_id,
    date(dr.run_at)                                        AS run_date,
    CASE WHEN c.run_id >= 241 THEN 'repair-on' ELSE 'repair-off' END AS regime,
    c.checked,
    c.failed,
    ROUND(100.0 * c.failed / NULLIF(c.checked, 0), 1)      AS fail_pct,
    s.shipped,
    c.checked - s.shipped                                  AS lost,
    MAX(0, c.failed - (c.checked - s.shipped))             AS failed_but_shipped,
    -- stories lost beyond what coherence flagged: something else dropped them
    MAX(0, (c.checked - s.shipped) - c.failed)             AS unexplained_loss,
    COALESCE(r.repair_calls, 0)                            AS repair_calls
FROM coh c
JOIN digest_runs dr ON dr.id = c.run_id
LEFT JOIN ship s ON s.run_id = c.run_id
LEFT JOIN rep  r ON r.run_id = c.run_id
ORDER BY c.run_id DESC;
