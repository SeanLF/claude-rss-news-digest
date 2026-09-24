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
--   Age is publisher-declared `published_raw` minus our `fetched_at`; feeds that lie
--   about or round their timestamps (some emit the fetch date) will compress ages
--   toward zero. Negative ages are clamped out, not investigated. Note the observed
--   max_h: the fetch recency window truncates the pool at roughly 28 hours, so this
--   measures freshness WITHIN that window and can never reveal staleness beyond it --
--   buckets are set at 3/6/12h for that reason, since a ">48h" bucket is dead by
--   construction.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM runs
),
pool AS (
    -- The first 19 characters as a UTC wall time: the fetcher writes UTC ISO 8601, so the offset
    -- cut off here is +00:00. A value that is not a valid timestamp (hour 25 passes a shape check;
    -- the fetcher keeps text Date.parse rejects) is NULL rather than failing the query: CASE, not a
    -- WHERE, because only CASE fixes the order, so the cast never sees such a value.
    SELECT a.run_id,
           a.title,
           a.source_id,
           CASE WHEN a.published_raw ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}'
                 AND pg_input_is_valid(REPLACE(SUBSTR(a.published_raw, 1, 19), 'T', ' '), 'timestamp') THEN
             EXTRACT(EPOCH FROM a.fetched_at
                     - (REPLACE(SUBSTR(a.published_raw, 1, 19), 'T', ' ')::timestamp AT TIME ZONE 'UTC'))
                 / 3600.0
           END AS age_hours
    FROM articles a, bounds b
    WHERE a.run_id >= b.lo AND a.published_raw <> ''
),
-- Single pass over the pool, tagging each article as shipped or not, rather than
-- joining a materialised pool CTE to the shipped set on `title`. There is no index
-- on articles(title); this form uses story_sources_run.
-- `pool_available` intentionally includes the shipped articles --
-- it is the full choice set, which is what makes it a baseline.
clean AS (
    SELECT p.age_hours,
           EXISTS (SELECT 1 FROM story_sources ss
                    WHERE ss.run_id = p.run_id
                      AND ss.source_title = p.title) AS was_shipped
    FROM pool p
    WHERE p.age_hours IS NOT NULL AND p.age_hours BETWEEN 0 AND 720
),
shipped AS (
    SELECT DISTINCT ss.run_id, ss.source_title
    FROM story_sources ss, bounds b
    WHERE ss.run_id >= b.lo AND ss.source_title IS NOT NULL
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
    COUNT(*)                                                 AS n,
    ROUND(MIN(age_hours), 1)                                 AS min_h,
    ROUND(AVG(age_hours), 1)                                 AS mean_h,
    ROUND(MAX(age_hours), 1)                                 AS max_h,
    ROUND(100.0 * SUM((age_hours <=  3)::int) / COUNT(*), 1) AS pct_under_3h,
    ROUND(100.0 * SUM((age_hours <=  6)::int) / COUNT(*), 1) AS pct_under_6h,
    ROUND(100.0 * SUM((age_hours >  12)::int) / COUNT(*), 1) AS pct_over_12h,
    (SELECT ROUND(100.0 * (SELECT COUNT(*) FROM shipped_aged)
                        / NULLIF((SELECT COUNT(*) FROM shipped), 0), 1)) AS matched_pct
FROM q
GROUP BY cohort
ORDER BY cohort COLLATE "C" DESC;
