-- Persist the per-stage REQUEST CONFIG (thinking, effort) in run_usage, not just the model.
--
-- Why: run_usage recorded WHAT model ran but never HOW it was configured, and on 2026-08-30 that
-- cost a whole measurement. A 60-run model comparison silently ran every arm with
-- thinking={"type":"disabled"} inherited from orchestrate._THINKING, and the confound was
-- invisible in the pipeline's own telemetry -- it took a separate prompt audit to find. Thinking
-- turned out to be a LARGER lever than model choice on COHERENCE (recall 0.761 -> 0.919,
-- p=0.002, and 26% cheaper), so the one variable that mattered most was the one not recorded.
--
-- Both columns are the RESOLVED value actually sent to the SDK (spec.thinking or the module
-- default; spec.effort or None), never the frontmatter text -- the whole point is to capture what
-- ran, not what someone intended. Nullable TEXT; historical rows backfill as NULL, which reads
-- correctly as "not recorded" rather than as a value.
--
-- Deliberately NOT a lookup table or a JSON blob: two scalar columns keep the analytics queries
-- greppable and let a config change show up in a GROUP BY the same way `model` already does.
ALTER TABLE run_usage ADD COLUMN thinking TEXT;
ALTER TABLE run_usage ADD COLUMN effort TEXT;
