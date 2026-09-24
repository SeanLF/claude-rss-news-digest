-- bin/ops health [ID]: a run's per-source fetch results, failures first.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
\getenv rid OPS_RUN
SELECT coalesce(nullif($1::text, '')::bigint, (SELECT max(id) FROM runs)) AS run_id \bind :rid \gset
SELECT json_build_object('run_id', $1::bigint, 'rows', coalesce(json_agg(q), '[]'::json))
FROM (
  SELECT source_id, is_success, articles_fetched, articles_kept, error
  FROM source_fetches WHERE run_id = $1::bigint ORDER BY is_success, source_id COLLATE "C"
) q \bind :run_id \g
