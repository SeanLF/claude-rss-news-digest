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
--   have no model_calls rows at all and are excluded rather than shown as $0.00. Runs
--   mailed before Resend broadcasts have no send (their recipient counts live in Resend),
--   so their per-subscriber columns are blank.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM runs
),
per_run AS (
    SELECT
        r.id                                                                  AS run_id,
        (r.started_at AT TIME ZONE 'UTC')::date                               AS run_date,
        (SELECT SUM(api_cost_usd) FROM model_calls mc WHERE mc.run_id = r.id) AS cost,
        (SELECT COUNT(DISTINCT headline) FROM story_sources ss
          WHERE ss.run_id = r.id)                                             AS shipped,
        r.articles_kept                                                       AS kept,
        (SELECT SUM(recipients) FROM sends s WHERE s.run_id = r.id AND s.status IN ('queued', 'sending', 'sent'))           AS recipients,
        EXTRACT(EPOCH FROM (SELECT MAX(ended_at) FROM run_attempts a WHERE a.run_id = r.id)
                           - r.started_at) / 60.0                             AS minutes
    FROM runs r, bounds b
    WHERE r.id >= b.lo AND r.status = 'completed'
)
SELECT
    run_id, run_date,
    ROUND(cost::numeric, 3)                                 AS cost_usd,
    shipped                                                 AS stories_shipped,
    ROUND((cost / NULLIF(shipped, 0))::numeric, 4)          AS usd_per_story,
    ROUND((cost / NULLIF(recipients, 0))::numeric, 4)       AS usd_per_subscriber,
    ROUND((cost / NULLIF(recipients, 0) * 30)::numeric, 2)  AS usd_per_sub_per_month,
    ROUND((1000.0 * cost / NULLIF(kept, 0))::numeric, 3)    AS usd_per_1k_articles_in,
    ROUND(minutes, 1)                                       AS wall_minutes
FROM per_run
WHERE cost IS NOT NULL
ORDER BY run_id DESC;
