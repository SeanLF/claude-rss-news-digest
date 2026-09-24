-- bin/ops artifacts [ID]: a run's archived artifacts, every status, with their sizes.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
\getenv rid OPS_RUN
SELECT coalesce(nullif($1::text, '')::bigint, (SELECT max(id) FROM runs)) AS run_id \bind :rid \gset
SELECT json_build_object('run_id', $1::bigint, 'rows', coalesce(json_agg(q), '[]'::json))
FROM (
  SELECT name, status, octet_length(content) AS bytes, attempt_id
  FROM artifacts WHERE run_id = $1::bigint ORDER BY name COLLATE "C", id
) q \bind :run_id \g
