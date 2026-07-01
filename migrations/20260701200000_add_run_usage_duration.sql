-- Persist per-stage wall-clock latency in run_usage. duration_ms is already computed by the SDK
-- (StageResult.duration_ms) and logged per stage, but was thrown away at record time -- so
-- per-stage latency (the real constraint on a subscription, alongside the weekly usage pool) was
-- not queryable for monitoring. Nullable; historical rows backfill as NULL.
ALTER TABLE run_usage ADD COLUMN duration_ms INTEGER;
