-- QUESTION: Which pipeline stage owns the spend, and has any stage's share of the
--   bill moved between the previous window and the current one?
-- WHY: bin/usage already prints the per-subagent token table; this deliberately does
--   NOT repeat it. The question here is drift -- which stage to attack next, and
--   whether a prompt or model change actually moved the cost it was supposed to move.
--   Share-of-total is the right unit because absolute cost tracks feed volume.
-- CAVEAT: Stage names are historical: `fact-check`/`summarize`/`assign`/`dispatcher`
--   belong to the pre-orchestrate.py architecture and stop appearing around run 200,
--   so a long window mixes two pipelines and a stage can show a -100% drift simply
--   because it no longer exists. `duration_ms` is NULL before run 220, so avg_ms is
--   blank for older stages rather than zero. Cost is API-equivalent, see
--   cost-per-shipped-story.sql.
-- PARAMS: runs (size of EACH window, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS recent_lo,
           MAX(id) - 2 * :runs + 1 AS prior_lo,
           MAX(id) - :runs AS prior_hi
    FROM digest_runs
),
tagged AS (
    SELECT ru.subagent, ru.model, ru.api_cost_usd, ru.duration_ms, ru.run_id,
           CASE WHEN ru.run_id >= b.recent_lo THEN 'recent'
                WHEN ru.run_id BETWEEN b.prior_lo AND b.prior_hi THEN 'prior' END AS bucket
    FROM run_usage ru, bounds b
    WHERE ru.run_id >= b.prior_lo
),
totals AS (
    SELECT bucket, SUM(api_cost_usd) AS total FROM tagged
    WHERE bucket IS NOT NULL GROUP BY bucket
),
agg AS (
    SELECT t.subagent, t.bucket,
           SUM(t.api_cost_usd) AS cost,
           COUNT(DISTINCT t.run_id) AS runs,
           AVG(t.duration_ms) AS avg_ms,
           100.0 * SUM(t.api_cost_usd) / NULLIF((SELECT total FROM totals WHERE bucket = t.bucket), 0) AS share
    FROM tagged t WHERE t.bucket IS NOT NULL GROUP BY t.subagent, t.bucket
)
SELECT
    subagent                                                         AS stage,
    MAX(CASE WHEN bucket = 'recent' THEN runs END)                   AS recent_runs,
    ROUND(MAX(CASE WHEN bucket = 'recent' THEN cost END), 3)         AS recent_usd,
    ROUND(MAX(CASE WHEN bucket = 'recent' THEN cost / runs END), 4)  AS usd_per_run,
    ROUND(MAX(CASE WHEN bucket = 'prior'  THEN share END), 1)        AS prior_share_pct,
    ROUND(MAX(CASE WHEN bucket = 'recent' THEN share END), 1)        AS recent_share_pct,
    ROUND(MAX(CASE WHEN bucket = 'recent' THEN share END)
        - MAX(CASE WHEN bucket = 'prior'  THEN share END), 1)        AS share_delta_pp,
    CAST(ROUND(MAX(CASE WHEN bucket = 'recent' THEN avg_ms END)) AS INTEGER) AS recent_avg_ms
FROM agg
GROUP BY subagent
ORDER BY COALESCE(MAX(CASE WHEN bucket = 'recent' THEN share END), -1) DESC;
