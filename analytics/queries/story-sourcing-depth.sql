-- QUESTION: How many independent sources back each shipped story, and how often does
--   a story rest on a single outlet?
-- WHY: The product's stated identity is transparency and verifiability, and the
--   concrete version of that promise is "more than one outlet saw this". A
--   single-source story is the one most likely to carry an unchecked claim into the
--   digest, and it is also the one a reader cannot triangulate. This tracks the rate
--   over time so a drift toward thin sourcing is visible before a correction is.
-- CAVEAT: Distinct source_id is not distinct REPORTING -- several feeds can be
--   reprinting the same wire copy, so this over-counts independence, and the
--   `perspective` field in sources.json (which marks wire reposts) is not in the
--   database to correct for it. Nor does source count measure whether the claims in
--   the summary are actually supported by those sources; that is coherence's job, not
--   this query's. Depth also tracks cluster size, so a thin day looks like thin
--   sourcing.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM runs
),
story AS (
    -- A headline carries one tier within a run, but nothing enforces it: MIN picks one where it
    -- does not.
    SELECT ss.run_id, ss.headline, MIN(ss.tier) AS tier,
           COUNT(DISTINCT ss.source_id) AS sources
    FROM story_sources ss, bounds b
    WHERE ss.run_id >= b.lo
    GROUP BY ss.run_id, ss.headline
),
rolled AS (
    SELECT 0 AS sort_key, COALESCE(tier, '(none)') AS tier, sources FROM story
    UNION ALL
    SELECT 1 AS sort_key, 'ALL' AS tier, sources FROM story
)
SELECT
    tier,
    COUNT(*)                                              AS stories,
    ROUND(AVG(sources), 2)                                AS mean_sources,
    MIN(sources)                                          AS min_sources,
    MAX(sources)                                          AS max_sources,
    SUM((sources = 1)::int)                               AS single_source,
    ROUND(100.0 * SUM((sources = 1)::int) / COUNT(*), 1)  AS pct_single_source,
    ROUND(100.0 * SUM((sources >= 3)::int) / COUNT(*), 1) AS pct_3plus_sources
FROM rolled
GROUP BY sort_key, tier
ORDER BY sort_key, tier COLLATE "C";
