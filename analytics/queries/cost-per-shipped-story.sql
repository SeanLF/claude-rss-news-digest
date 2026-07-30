-- QUESTION: What does one run, one shipped story, and one subscriber-digest actually
--   cost, and is that cost trending?
-- WHY: This is the unit-economics line for a project that runs daily and forever. Cost
--   per ARTICLE INGESTED (`articles_kept`, ~550 a run) and cost per story the reader
--   sees (~16 a run) differ by ~35x, so the denominator has to be stated, not assumed --
--   /stats published the ingest rate under a "Cost / story" label until 2026-07-30 and
--   now shows both. This query is the per-run series behind that page's window average,
--   and the reader-facing figure is the one that decides whether a model or stage change
--   is affordable.
-- CAVEAT: `api_cost_usd` is the SDK's API-equivalent cost, not an invoice -- if the
--   pipeline runs under a subscription the real marginal cost is zero. Cost is
--   attributed to the run that recorded it; the thread-synthesis rows are recorded in
--   a separate call and are fail-soft, so a run can under-report. Runs before 106
--   have no run_usage rows at all and are excluded rather than shown as $0.00.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
per_run AS (
    SELECT
        dr.id,
        date(dr.run_at) AS run_date,
        (SELECT SUM(api_cost_usd) FROM run_usage ru WHERE ru.run_id = dr.id)  AS cost,
        (SELECT COUNT(DISTINCT headline) FROM shown_narratives sn
          WHERE sn.run_id = dr.id)                                            AS shipped,
        dr.articles_kept                                                      AS kept,
        dr.articles_emailed                                                   AS recipients,
        (julianday(dr.completed_at) - julianday(dr.run_at)) * 1440.0          AS minutes
    FROM digest_runs dr, bounds b
    WHERE dr.id >= b.lo AND dr.status = 'completed'
)
SELECT
    run_id, run_date,
    ROUND(cost, 3)                                          AS cost_usd,
    shipped                                                 AS stories_shipped,
    ROUND(cost / NULLIF(shipped, 0), 4)                     AS usd_per_story,
    ROUND(cost / NULLIF(recipients, 0), 4)                  AS usd_per_subscriber,
    ROUND(cost / NULLIF(recipients, 0) * 30, 2)             AS usd_per_sub_per_month,
    ROUND(1000.0 * cost / NULLIF(kept, 0), 3)               AS usd_per_1k_articles_in,
    ROUND(minutes, 1)                                       AS wall_minutes
FROM (SELECT id AS run_id, * FROM per_run)
WHERE cost IS NOT NULL
ORDER BY run_id DESC;
