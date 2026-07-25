-- QUESTION: Is SELECT exercising editorial judgment, or is it mostly picking whichever
--   cluster has the most articles in it?
-- WHY: If selection probability tracks cluster size almost perfectly, the expensive
--   editorial stage is an ornate argmax over "how many outlets covered this", and the
--   digest is a popularity ranking that a five-line Python function could reproduce.
--   If small clusters get selected at a meaningful rate, the stage is adding
--   something. This is the cheapest available test of whether the most expensive
--   judgment in the pipeline is doing work.
-- CAVEAT: This is correlational and cannot separate "SELECT prefers big clusters"
--   from "big clusters are genuinely the important stories" -- multi-outlet coverage
--   is a real newsworthiness signal, so high lift at the top is expected and not by
--   itself damning. The interesting cell is the selection rate for SMALL clusters: a
--   flat zero there means no scoops ever get through. Clusters are matched to
--   selections by cluster_index, which is positional -- it is valid only within the
--   same run's artifacts. Runs 204+ only.
-- PARAMS: runs (window size, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS lo FROM digest_runs
),
clusters AS (
    SELECT cr.run_id,
           c.key                                          AS cluster_index,
           json_array_length(c.value, '$.article_ids')    AS size
    FROM cluster_runs cr, json_each(cr.clusters_json, '$.clusters') c, bounds b
    WHERE cr.run_id >= b.lo
),
picked AS (
    SELECT ra.run_id, json_extract(s.value, '$.cluster_index') AS cluster_index, tier.k AS tier
    FROM run_artifacts ra, bounds b,
         (SELECT 'must_know' AS k UNION ALL SELECT 'should_know') tier,
         json_each(json_extract(ra.content, '$.' || tier.k)) s
    WHERE ra.artifact_name = 'selected.json' AND ra.run_id >= b.lo
),
joined AS (
    SELECT c.run_id, c.size,
           CASE WHEN p.cluster_index IS NULL THEN 0 ELSE 1 END AS selected,
           p.tier
    FROM clusters c
    LEFT JOIN picked p ON p.run_id = c.run_id AND p.cluster_index = c.cluster_index
)
SELECT
    CASE WHEN size = 1 THEN '1 (single outlet)'
         WHEN size <= 2 THEN '2'
         WHEN size <= 4 THEN '3-4'
         WHEN size <= 8 THEN '5-8'
         WHEN size <= 15 THEN '9-15'
         ELSE '16+' END                                    AS cluster_size,
    COUNT(*)                                               AS clusters,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct_of_clusters,
    SUM(selected)                                          AS selected,
    ROUND(100.0 * SUM(selected) / COUNT(*), 1)             AS selection_rate_pct,
    ROUND(100.0 * SUM(selected) / SUM(SUM(selected)) OVER (), 1) AS pct_of_selections,
    ROUND((1.0 * SUM(selected) / SUM(SUM(selected)) OVER ())
        / NULLIF(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 0), 2)  AS lift,
    SUM(tier = 'must_know')                                AS as_must_know
FROM joined
GROUP BY cluster_size
ORDER BY MIN(size);
