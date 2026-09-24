-- bin/ops artifact ID NAME: one current artifact's content, to stdout byte for byte. OPS_RUN and
-- OPS_NAME arrive through the environment and reach Postgres as bound parameters, never as SQL text.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
\getenv rid OPS_RUN
\getenv name OPS_NAME
SELECT coalesce(nullif($1::text, '')::bigint, (SELECT max(id) FROM runs)) AS run_id \bind :rid \gset
SELECT count(*) > 0 AS found FROM artifacts WHERE run_id = $1::bigint AND name = $2::text AND status = 'current' \bind :run_id :name \gset
\if :found
-- Through a variable and \echo -n, because a query's output always ends in a newline the content
-- may not have (its sha256 would then not match).
SELECT content FROM artifacts WHERE run_id = $1::bigint AND name = $2::text AND status = 'current' \bind :run_id :name \gset
\echo -n :content
\else
\warn no such artifact for run :run_id
-- A \gset that gets no row is an error, and ON_ERROR_STOP makes it the exit status (3).
SELECT FROM runs WHERE false \gset
\endif
