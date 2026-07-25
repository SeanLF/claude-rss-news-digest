-- QUESTION: Is the funnel's shape changing? Compare each stage's conversion rate in
--   the recent window against the window immediately before it.
-- WHY: A single run's funnel is noise. What matters operationally is drift: if
--   cluster->selected quietly halves, the digest gets thinner without anything
--   failing. This is the query that turns the funnel from a description into an
--   early-warning signal, because the baseline is built in rather than eyeballed.
-- CAVEAT: Two adjacent windows is a crude comparison -- a single anomalous run can
--   move a 30-run mean, and the two windows are not tested for significance. Feed
--   volume is seasonal (weekends are thinner), so windows that straddle different
--   numbers of weekends will differ for reasons that have nothing to do with the
--   pipeline. Treat a <10% delta as noise. Runs missing ANY stage are excluded
--   entirely (see `complete` below), so `prior_runs` can be far smaller than the
--   window -- if it is, the prior column is a handful of runs and not a baseline.
-- PARAMS: runs (size of EACH window, default 30)

WITH bounds AS (
    SELECT MAX(id) - :runs + 1 AS recent_lo,
           MAX(id) - 2 * :runs + 1 AS prior_lo,
           MAX(id) - :runs AS prior_hi
    FROM digest_runs
),
per_run AS (
    SELECT
        dr.id,
        CASE WHEN dr.id >= b.recent_lo THEN 'recent'
             WHEN dr.id >= b.prior_lo AND dr.id <= b.prior_hi THEN 'prior' END AS bucket,
        (SELECT SUM(articles_fetched) FROM source_health sh WHERE sh.run_id = dr.id) AS rss_items,
        dr.articles_kept AS kept,
        (SELECT json_array_length(clusters_json, '$.clusters')
           FROM cluster_runs cr WHERE cr.run_id = dr.id)                            AS clusters,
        (SELECT json_array_length(content, '$.must_know')
              + json_array_length(content, '$.should_know')
           FROM run_artifacts ra
          WHERE ra.run_id = dr.id AND ra.artifact_name = 'selected.json')           AS selected,
        (SELECT COUNT(DISTINCT headline) FROM shown_narratives sn WHERE sn.run_id = dr.id) AS shipped
    FROM digest_runs dr, bounds b
    WHERE dr.id >= b.prior_lo AND dr.status = 'completed'
),
-- Only runs with EVERY stage recorded may enter the aggregate. Without this,
-- SUM() skips NULL artifact rows in one term but not another, and the ratio
-- silently compares different sets of runs (this produced a 708% conversion
-- rate before it was caught).
complete AS (
    SELECT * FROM per_run
    WHERE bucket IS NOT NULL
      AND rss_items IS NOT NULL AND kept IS NOT NULL
      AND clusters IS NOT NULL AND selected IS NOT NULL AND shipped IS NOT NULL
),
agg AS (
    SELECT bucket,
           COUNT(*) AS n_runs,
           SUM(rss_items) AS rss_items,
           SUM(kept)      AS kept,
           SUM(clusters)  AS clusters,
           SUM(selected)  AS selected,
           SUM(shipped)   AS shipped
    FROM complete GROUP BY bucket
),
stages(step, stage) AS (
    VALUES (1, 'rss -> kept'), (2, 'kept -> clusters'),
           (3, 'clusters -> selected'), (4, 'selected -> shipped')
),
rates AS (
    SELECT s.step, s.stage, a.bucket,
           CASE s.step
               WHEN 1 THEN 100.0 * a.kept     / NULLIF(a.rss_items, 0)
               WHEN 2 THEN 100.0 * a.clusters / NULLIF(a.kept, 0)
               WHEN 3 THEN 100.0 * a.selected / NULLIF(a.clusters, 0)
               WHEN 4 THEN 100.0 * a.shipped  / NULLIF(a.selected, 0)
           END AS pct
    FROM stages s CROSS JOIN agg a
)
SELECT
    r.stage,
    (SELECT n_runs FROM agg WHERE bucket = 'prior')  AS prior_runs,
    ROUND(MAX(CASE WHEN r.bucket = 'prior'  THEN r.pct END), 2) AS prior_pct,
    (SELECT n_runs FROM agg WHERE bucket = 'recent') AS recent_runs,
    ROUND(MAX(CASE WHEN r.bucket = 'recent' THEN r.pct END), 2) AS recent_pct,
    ROUND(MAX(CASE WHEN r.bucket = 'recent' THEN r.pct END)
        - MAX(CASE WHEN r.bucket = 'prior'  THEN r.pct END), 2) AS delta_pp,
    ROUND(100.0 * (MAX(CASE WHEN r.bucket = 'recent' THEN r.pct END)
                 - MAX(CASE WHEN r.bucket = 'prior'  THEN r.pct END))
        / NULLIF(MAX(CASE WHEN r.bucket = 'prior' THEN r.pct END), 0), 1) AS delta_rel_pct
FROM rates r
GROUP BY r.step, r.stage
ORDER BY r.step;
