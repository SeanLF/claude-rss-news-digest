-- QUESTION: How old is the news we actually ship, and is that older or fresher than
--   the pool of articles we had available to choose from?
-- WHY: "Our median shipped story is 9 hours old" is meaningless alone. The decision
--   it informs is whether to move the run time or widen the fetch window, and that
--   only follows if shipped news is systematically STALER than what was on offer --
--   which would mean the curation is biased toward stories that have had time to
--   accumulate coverage. The pool baseline is computed in the same query so the two
--   numbers are always comparable.
-- CAVEAT: Shipped articles are matched to the fetched pool by exact title within the
--   same run; roughly 15% of shipped rows do not match (title rewritten upstream,
--   or the source article was fetched in an earlier run), so `matched_pct` is
--   reported -- read the freshness numbers as describing the matched subset only.
--   Age is publisher-declared `published` minus our `fetched_at`; feeds that lie
--   about or round their timestamps (some emit the fetch date) will compress ages
--   toward zero. Negative ages are clamped out, not investigated. Note the observed
--   max_h: the fetch recency window truncates the pool at roughly 28 hours, so this
--   measures freshness WITHIN that window and can never reveal staleness beyond it --
--   buckets are set at 3/6/12h for that reason, since a ">48h" bucket is dead by
--   construction.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
pool AS (
    SELECT fa.run_id,
           fa.title,
           fa.source_id,
           (julianday(fa.fetched_at) - julianday(REPLACE(SUBSTR(fa.published, 1, 19), 'T', ' ')))
               * 24.0 AS age_hours
    FROM fetched_articles fa, bounds b
    WHERE fa.run_id >= b.lo AND fa.published <> ''
),
-- Single pass over the pool, tagging each article as shipped or not, rather than
-- joining a materialised pool CTE to the shipped set on `title`. There is no index
-- on fetched_articles(title), so that join was a repeated scan and cost ~540 ms at
-- runs=30 and ~3.8 s over full history; this form uses idx_shown_narratives_run and
-- runs in a few ms. `pool_available` intentionally includes the shipped articles --
-- it is the full choice set, which is what makes it a baseline.
clean AS (
    SELECT p.age_hours,
           EXISTS (SELECT 1 FROM shown_narratives sn
                    WHERE sn.run_id = p.run_id
                      AND sn.original_title = p.title) AS was_shipped
    FROM pool p
    WHERE p.age_hours IS NOT NULL AND p.age_hours BETWEEN 0 AND 720
),
shipped AS (
    SELECT DISTINCT sn.run_id, sn.original_title
    FROM shown_narratives sn, bounds b
    WHERE sn.run_id >= b.lo AND sn.original_title IS NOT NULL
),
shipped_aged AS (
    SELECT age_hours FROM clean WHERE was_shipped
),
q AS (
    SELECT 'shipped' AS cohort, age_hours FROM shipped_aged
    UNION ALL
    SELECT 'pool_available', age_hours FROM clean
)
SELECT
    cohort,
    COUNT(*)                                          AS n,
    ROUND(MIN(age_hours), 1)                          AS min_h,
    ROUND(AVG(age_hours), 1)                          AS mean_h,
    ROUND(MAX(age_hours), 1)                          AS max_h,
    ROUND(100.0 * SUM(age_hours <=  3) / COUNT(*), 1) AS pct_under_3h,
    ROUND(100.0 * SUM(age_hours <=  6) / COUNT(*), 1) AS pct_under_6h,
    ROUND(100.0 * SUM(age_hours >  12) / COUNT(*), 1) AS pct_over_12h,
    (SELECT ROUND(100.0 * (SELECT COUNT(*) FROM shipped_aged)
                        / NULLIF((SELECT COUNT(*) FROM shipped), 0), 1)) AS matched_pct
FROM q
GROUP BY cohort
ORDER BY cohort DESC;
