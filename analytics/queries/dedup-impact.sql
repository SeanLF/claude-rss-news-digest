-- QUESTION: How much work is dedup actually doing, and how much of it is happening in
--   the marginal similarity band where it is known to be wrong most of the time?
-- WHY: Cross-day dedup at a 0.35 TF-IDF threshold has a measured ~65% false positive
--   rate, so every decision near it is more likely to be a wrongly-suppressed story
--   than a real duplicate. This sizes the exposure: what share of filtered articles
--   sit in the marginal band, what share of intake is removed, and -- via first_run
--   and last_run -- WHICH thresholds are actually still live. A threshold whose
--   last_run is far behind the newest run has been retired, and its numbers are
--   history, not current exposure.
-- CAVEAT: dedup_log records only FILTERED articles -- there is no row for an article
--   the filter passed. So this can measure the composition and volume of suppression
--   but CANNOT compute a precision or recall, and `pct_of_intake` is against the
--   fetched pool, not against the true duplicate rate. The 65% false-positive figure
--   comes from a separate manual audit, not from this table. Two thresholds coexist
--   (0.8 same-day wire-repost collapse, 0.35 cross-day) and mean different things --
--   they are reported separately for that reason, never pooled. `per_run` divides by
--   every completed run in the window, NOT by the runs where that threshold appears --
--   dividing by the latter made a retired threshold look 30x more active than it is.
--   Check first_run/last_run before quoting any row as current behaviour.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
d AS (
    SELECT dl.* FROM dedup_log dl, bounds b WHERE dl.run_id >= b.lo
),
intake AS (
    SELECT SUM(articles_kept) AS kept, COUNT(*) AS n_runs
    FROM digest_runs, bounds
    WHERE id >= bounds.lo AND status = 'completed'
)
SELECT
    d.threshold,
    CASE d.threshold WHEN 0.8 THEN 'same-day wire repost'
                     WHEN 0.35 THEN 'cross-day TF-IDF'
                     ELSE 'other' END                            AS filter_kind,
    MIN(d.run_id)                                                AS first_run,
    MAX(d.run_id)                                                AS last_run,
    COUNT(DISTINCT d.run_id)                                     AS runs_present,
    CASE WHEN MAX(d.run_id) < (SELECT MAX(id) FROM digest_runs) - 2
         THEN 'RETIRED' ELSE 'live' END                          AS state,
    COUNT(*)                                                     AS filtered,
    ROUND(1.0 * COUNT(*) / (SELECT n_runs FROM intake), 1)       AS per_run,
    ROUND(100.0 * COUNT(*) / (SELECT kept FROM intake), 2)       AS pct_of_intake,
    SUM(d.similarity < 0.50)                                     AS band_marginal,
    ROUND(100.0 * SUM(d.similarity < 0.50) / COUNT(*), 1)        AS pct_marginal,
    SUM(d.similarity >= 0.50 AND d.similarity < 0.80)            AS band_mid,
    SUM(d.similarity >= 0.80)                                    AS band_confident,
    SUM(d.similarity >= 0.999)                                   AS band_exact,
    ROUND(AVG(d.similarity), 3)                                  AS avg_similarity
FROM d
GROUP BY d.threshold
ORDER BY d.threshold;
