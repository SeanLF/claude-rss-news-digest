-- bin/ops run [ID]: one run's row (default: the latest), as JSON. OPS_RUN arrives through the
-- environment and reaches Postgres as a bound parameter, never as SQL text.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
\getenv rid OPS_RUN
SELECT coalesce(nullif($1::text, '')::bigint, (SELECT max(id) FROM runs)) AS run_id \bind :rid \gset
SELECT json_build_object('run_id', $1::bigint, 'rows', coalesce(json_agg(q), '[]'::json))
FROM (
  SELECT r.id, r.started_at, r.articles_kept, r.status, r.outcome, r.git_sha, r.error,
         (SELECT count(*) FROM run_attempts a WHERE a.run_id = r.id) AS attempts,
         (SELECT max(ended_at) FROM run_attempts a WHERE a.run_id = r.id) AS ended_at,
         (SELECT sum(recipients) FROM sends s WHERE s.run_id = r.id AND s.status IN ('queued', 'sending', 'sent')) AS recipients
  FROM runs r WHERE r.id = $1::bigint
) q \bind :run_id \g
