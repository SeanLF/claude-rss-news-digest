-- bin/ops usage [ID]: a run's model calls with tokens and API-equivalent cost, costliest first.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
\getenv rid OPS_RUN
SELECT coalesce(nullif($1::text, '')::bigint, (SELECT max(id) FROM runs)) AS run_id \bind :rid \gset
SELECT json_build_object('run_id', $1::bigint, 'rows', coalesce(json_agg(q), '[]'::json))
FROM (
  SELECT stage, branch, request_model AS model, input_tokens, output_tokens, cache_read_input_tokens,
         round(api_cost_usd::numeric, 4) AS cost_usd, duration_ms, thinking, effort, outcome
  FROM model_calls WHERE run_id = $1::bigint ORDER BY api_cost_usd DESC, id
) q \bind :run_id \g
