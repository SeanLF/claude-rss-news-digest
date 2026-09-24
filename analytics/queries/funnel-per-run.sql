-- QUESTION: For each recent run, how many items survive each stage from RSS item
--   to shipped story, and where exactly does the 99%+ attrition happen?
-- WHY: The drop-offs are the pipeline's core economics and nobody can currently say
--   which losses are deliberate editorial curation and which are silent. This
--   separates the recency-window drop (huge, mechanical) from dedup (small), from
--   clustering, from SELECT's editorial cut, from post-write coherence loss.
-- CAVEAT: `window_dropped` is a RESIDUAL (rss_items - kept - dedup_filtered), not a
--   measured number -- it absorbs the recency filter plus any other pre-archive drop
--   and any per-source fetch error that still reported a count. `clusters` and
--   `selected` come from artifacts, which only exists from run 204 onward, so
--   older runs show NULL there rather than zero. Those artifact writes are fail-soft:
--   a NULL can mean "the archive write failed", not "the stage produced nothing".
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM runs
),
rss AS (
    SELECT run_id, SUM(articles_fetched) AS rss_items
    FROM source_fetches, bounds
    WHERE run_id >= bounds.lo GROUP BY run_id
),
dedup AS (
    SELECT run_id, COUNT(*) AS dedup_filtered
    FROM dedup_matches, bounds
    WHERE run_id >= bounds.lo GROUP BY run_id
),
clusters AS (
    SELECT run_id, jsonb_array_length(content::jsonb -> 'clusters') AS clusters
    FROM artifacts, bounds
    WHERE name = 'clusters.json' AND status = 'current' AND run_id >= bounds.lo
),
selected AS (
    SELECT run_id,
           jsonb_array_length(content::jsonb -> 'must_know')
         + jsonb_array_length(content::jsonb -> 'should_know') AS selected
    FROM artifacts, bounds
    WHERE name = 'selected.json' AND status = 'current' AND run_id >= bounds.lo
),
shipped AS (
    SELECT run_id, COUNT(DISTINCT headline) AS shipped
    FROM story_sources, bounds
    WHERE run_id >= bounds.lo GROUP BY run_id
)
SELECT
    r.id                                                    AS run_id,
    (r.started_at AT TIME ZONE 'UTC')::date                 AS run_date,
    rss.rss_items,
    r.articles_kept                                         AS kept,
    COALESCE(dedup.dedup_filtered, 0)                       AS dedup_filtered,
    rss.rss_items - r.articles_kept
        - COALESCE(dedup.dedup_filtered, 0)                 AS window_dropped,
    clusters.clusters,
    selected.selected,
    shipped.shipped,
    ROUND(100.0 * r.articles_kept / NULLIF(rss.rss_items, 0), 1)  AS pct_kept,
    ROUND(100.0 * shipped.shipped / NULLIF(rss.rss_items, 0), 3)  AS pct_shipped,
    ROUND(1.0 * r.articles_kept / NULLIF(clusters.clusters, 0), 1) AS articles_per_cluster
FROM runs r
CROSS JOIN bounds
LEFT JOIN rss      ON rss.run_id      = r.id
LEFT JOIN dedup    ON dedup.run_id    = r.id
LEFT JOIN clusters ON clusters.run_id = r.id
LEFT JOIN selected ON selected.run_id = r.id
LEFT JOIN shipped  ON shipped.run_id  = r.id
WHERE r.id >= bounds.lo AND r.status = 'completed'
ORDER BY r.id DESC;
