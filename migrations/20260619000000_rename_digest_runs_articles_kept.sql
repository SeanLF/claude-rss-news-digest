-- Rename digest_runs.articles_fetched -> articles_kept.
--
-- The column has always stored the KEPT count (run.py passes fetch_result.total_kept
-- into complete_run), so "articles_fetched" was a misnomer. RENAME COLUMN preserves
-- every existing value, so historical /stats rows keep their counts -- the column is
-- a deliberate per-run denormalization of SUM(source_health.articles_kept), kept as a
-- durable snapshot that doesn't depend on source_health row/FK completeness.
--
-- source_health has its own, genuinely distinct articles_fetched/articles_kept pair;
-- that table is NOT touched here -- only digest_runs.
--
-- Deploy note: bin/deploy applies migrations AFTER swapping containers, so the new
-- circulation binary reads articles_kept for the few seconds before this rename lands.
-- /stats is an on-demand read on an internal dashboard, so a 500 only occurs if the
-- page is loaded in that window during a self-initiated deploy -- effectively nil. A
-- zero-downtime expand/contract isn't warranted for this surface.
--
-- Requires SQLite >= 3.25.0 (RENAME COLUMN).

ALTER TABLE digest_runs RENAME COLUMN articles_fetched TO articles_kept;
