-- QUESTION: Which feeds are unreliable, and -- more importantly -- which ones are
--   "succeeding" while returning nothing?
-- WHY: The /stats page already shows a per-source success rate. That metric cannot
--   see the worse failure mode: a feed that returns HTTP 200 and zero items, which
--   looks perfectly healthy and silently removes a source from the digest. This adds
--   the zero-yield rate and the current consecutive-failure streak, which is what you
--   act on (drop the feed, fix the user-agent, or find a replacement).
-- CAVEAT: `articles_fetched` is NULL for ~1,283 older source_health rows (the column
--   was added later), and those are excluded from yield stats rather than counted as
--   zero -- so `runs_measured` can be lower than `attempts`. A zero-yield run is not
--   necessarily a bug: a low-volume feed like The Economist's regional sections
--   genuinely publishes nothing some days. Compare within a tier of feed, not across.
--   A very low `keep_pct` on a high `avg_fetched_ok` is not a fault either -- it means
--   the feed serves a deep archive and the recency window discards nearly all of it --
--   but it IS wasted fetch and parse work worth knowing about.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
h AS (
    SELECT sh.* FROM source_health sh, bounds b WHERE sh.run_id >= b.lo
),
streak AS (
    -- consecutive failures counting back from each source's most recent attempt
    SELECT source_id, COUNT(*) AS current_fail_streak
    FROM (
        SELECT h1.source_id, h1.run_id
        FROM h h1
        WHERE h1.success = 0
          AND h1.run_id > COALESCE(
                (SELECT MAX(h2.run_id) FROM h h2
                  WHERE h2.source_id = h1.source_id AND h2.success = 1), 0)
    ) GROUP BY source_id
)
SELECT
    h.source_id,
    COUNT(*)                                                        AS attempts,
    ROUND(100.0 * SUM(h.success) / COUNT(*), 1)                     AS success_pct,
    COALESCE(s.current_fail_streak, 0)                              AS fail_streak,
    SUM(h.articles_fetched IS NOT NULL)                             AS runs_measured,
    SUM(CASE WHEN h.success = 1 AND h.articles_fetched = 0 THEN 1 ELSE 0 END) AS zero_yield_runs,
    ROUND(100.0 * SUM(CASE WHEN h.success = 1 AND h.articles_fetched = 0 THEN 1 ELSE 0 END)
        / NULLIF(SUM(h.articles_fetched IS NOT NULL), 0), 1)        AS zero_yield_pct,
    -- averaged over SUCCESSFUL attempts only: including the zeros from failed
    -- fetches would blend "the feed is broken" into "the feed is quiet"
    ROUND(AVG(CASE WHEN h.success = 1 THEN h.articles_fetched END), 1) AS avg_fetched_ok,
    ROUND(AVG(CASE WHEN h.success = 1 THEN h.articles_kept END), 1)    AS avg_kept_ok,
    ROUND(100.0 * SUM(CASE WHEN h.success = 1 THEN h.articles_kept END)
        / NULLIF(SUM(CASE WHEN h.success = 1 THEN h.articles_fetched END), 0), 1) AS keep_pct,
    substr(MAX(COALESCE(h.error_message, '')), 1, 40)               AS last_error
FROM h LEFT JOIN streak s ON s.source_id = h.source_id
GROUP BY h.source_id
ORDER BY success_pct ASC, zero_yield_pct DESC;
