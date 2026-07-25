-- QUESTION: The digest raises open questions on evolving stories. How many ever get
--   answered, how fast, and how many are just quietly abandoned?
-- WHY: An open question printed to a reader is a promise that the digest is following
--   the story. If three quarters are never resolved, the feature is generating
--   plausible-sounding loose ends rather than tracking them, and that is a product
--   defect the reader can see even though nothing errors. The age of the still-open
--   questions is the tell: genuinely pending questions are recent, abandoned ones are
--   old.
-- CAVEAT: "Resolved" means the pipeline marked it resolved, not that the world
--   answered it -- there is no verification that `resolved_how` is accurate, and a
--   model with an incentive to close loops may over-resolve. Unresolved is also the
--   correct state for a genuinely unresolved question, so a low resolution rate is
--   not automatically failure; read it together with `mean_age_days` and
--   `pct_on_dormant_thread` of the open set -- an open question on a dormant thread
--   is abandoned in all but name. Questions whose thread went dormant were never
--   explicitly closed, so they
--   sit in `open` forever and inflate the backlog.
-- PARAMS: (none -- describes the whole question population)

WITH q AS (
    SELECT tq.id, tq.status, tq.thread_id, tq.raised_run_id, tq.resolved_run_id,
           t.status AS thread_status,
           julianday('now') - julianday(tq.created_at) AS age_days,
           tq.resolved_run_id - tq.raised_run_id       AS runs_to_resolve
    FROM thread_questions tq
    LEFT JOIN threads t ON t.id = tq.thread_id
)
SELECT
    status,
    COUNT(*)                                                      AS questions,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM q), 1)         AS pct,
    COUNT(DISTINCT thread_id)                                     AS threads,
    ROUND(AVG(age_days), 1)                                       AS mean_age_days,
    ROUND(MAX(age_days), 1)                                       AS oldest_days,
    ROUND(AVG(runs_to_resolve), 1)                                AS mean_runs_to_resolve,
    MAX(runs_to_resolve)                                          AS max_runs_to_resolve,
    SUM(thread_status = 'dormant')                                AS on_dormant_thread,
    ROUND(100.0 * SUM(thread_status = 'dormant') / COUNT(*), 1)   AS pct_on_dormant_thread,
    SUM(age_days <= 7)                                            AS raised_last_7d
FROM q
GROUP BY status
ORDER BY status;
