-- QUESTION: Which sources get cited in the digest more (or less) than their share of
--   the article pool would predict?
-- WHY: "The Guardian is 6% of our citations" is not actionable. "The Guardian supplies
--   12.6% of the pool and earns 5.9% of citations -- a 0.47x lift" is: either the feed
--   is too noisy for its volume, or the curation has a blind spot. Lift is the honest
--   version of a source-mix chart because the availability baseline is in the same
--   row. It also subsumes the /stats page's binary "never-selected sources" list by
--   giving every source a magnitude instead of a yes/no.
-- CAVEAT: Lift measures citation share, not quality -- a wire service that supplies
--   many near-duplicate items will look under-selected by construction, because
--   clustering collapses them into one story that cites a subset. High-volume feeds
--   are therefore structurally penalised and a lift near 1.0 is not a target. A
--   source with a tiny pool (avail < 30) has a lift dominated by noise; the
--   `enough_data` column marks those. shown_narratives rows count one per
--   (story x source), so a story citing 12 sources contributes 12 rows.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
avail AS (
    SELECT source_id, COUNT(*) AS n
    FROM fetched_articles, bounds WHERE run_id >= bounds.lo GROUP BY source_id
),
cited AS (
    SELECT source_id, COUNT(*) AS n, COUNT(DISTINCT headline) AS stories
    FROM shown_narratives, bounds
    WHERE run_id >= bounds.lo AND source_id IS NOT NULL GROUP BY source_id
),
tot AS (
    SELECT (SELECT SUM(n) FROM avail) AS ta, (SELECT SUM(n) FROM cited) AS tc
)
SELECT
    a.source_id,
    a.n                                                     AS pool_articles,
    ROUND(100.0 * a.n / tot.ta, 2)                          AS pct_of_pool,
    COALESCE(c.n, 0)                                        AS citations,
    ROUND(100.0 * COALESCE(c.n, 0) / tot.tc, 2)             AS pct_of_citations,
    COALESCE(c.stories, 0)                                  AS distinct_stories,
    ROUND((1.0 * COALESCE(c.n, 0) / tot.tc)
        / NULLIF(1.0 * a.n / tot.ta, 0), 2)                 AS lift,
    ROUND(100.0 * COALESCE(c.n, 0) / a.n, 2)                AS pct_of_own_pool_cited,
    CASE WHEN a.n >= 30 THEN 'yes' ELSE 'thin' END          AS enough_data
FROM avail a
LEFT JOIN cited c ON c.source_id = a.source_id
CROSS JOIN tot
ORDER BY lift DESC;
