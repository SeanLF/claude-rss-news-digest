-- QUESTION: Do "evolving story threads" actually evolve, or does the overwhelming
--   majority get opened once and never continued?
-- WHY: The thread feature exists to give readers continuity across days. Its value is
--   entirely in the continuation rate -- a thread with one installment is an
--   ordinary story wearing a feature's costume, and it costs a synthesis call.
--   This distinguishes the two, so the feature can be judged on whether it earns its
--   spend rather than on whether it runs without erroring.
-- CAVEAT: Single-installment threads are not automatically waste -- a thread opened
--   yesterday has had no chance to continue yet, so the newest runs bias the
--   singleton rate upward. `days_span` is the calendar spread of installments, not
--   the number of digests the reader saw it in. Threads only exist from run 205 and
--   only when THREADS_ENABLED was on; thread processing is wrapped in a swallowing
--   exception handler, so a run with zero threads may be a crash, not a quiet day.
--   `active`/`dormant` are thread_state's, which counts published updates only.
-- PARAMS: (none -- describes the whole thread population)

WITH per_thread AS (
    SELECT
        t.id, ts.status,
        COUNT(tu.id)                                              AS installments,
        MIN(tu.run_id)                                            AS first_run,
        MAX(tu.run_id)                                            AS last_run,
        ROUND(EXTRACT(EPOCH FROM MAX(tu.created_at) - MIN(tu.created_at)) / 86400, 1) AS days_span,
        SUM((tu.content IS NOT NULL)::int)                        AS with_content
    FROM threads t
    LEFT JOIN thread_state ts ON ts.id = t.id
    LEFT JOIN thread_updates tu ON tu.thread_id = t.id
    GROUP BY t.id, ts.status
),
maxrun AS (SELECT MAX(run_id) AS mx FROM thread_updates)
SELECT
    CASE WHEN installments <= 1 THEN '1 (never continued)'
         WHEN installments <= 3 THEN '2-3'
         WHEN installments <= 7 THEN '4-7'
         ELSE '8+' END                                            AS installment_band,
    COUNT(*)                                                      AS threads,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM per_thread), 1) AS pct_of_threads,
    SUM(installments)                                             AS total_installments,
    ROUND(100.0 * SUM(installments)
        / (SELECT SUM(installments) FROM per_thread), 1)          AS pct_of_installments,
    SUM((status = 'active')::int)                                 AS active,
    SUM((status = 'dormant')::int)                                AS dormant,
    ROUND(AVG(days_span), 1)                                      AS avg_days_span,
    SUM(with_content)                                             AS installments_synthesized,
    -- threads opened in the last 3 runs have not had a fair chance to continue
    SUM((first_run > (SELECT mx FROM maxrun) - 3)::int)           AS too_new_to_judge
FROM per_thread
GROUP BY installment_band
ORDER BY MIN(installments);
