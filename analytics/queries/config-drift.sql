-- QUESTION: What request configuration is each stage actually running under, and has it changed?
-- WHY: On 2026-08-30 a 60-run model comparison silently ran every arm with thinking disabled,
--   inherited from a global constant whose justification had been void since 2026-07 (the stage it
--   was written for no longer sends a prompt to a model). Thinking then turned out to be a LARGER
--   lever than model choice on COHERENCE -- recall 0.761 -> 0.919, p=0.002, and 26% cheaper -- so
--   the single variable that mattered most was the one nothing recorded. This query is the read-out
--   that was missing. It is also the one thing an OTLP backend CANNOT replace: Claude Code's
--   telemetry does not emit extended-thinking configuration at all (feature requests #31585 and
--   #46118 both closed "not planned"), so the DB is the only place this can live. See
--   docs/2026-08-30-llm-tracing-backend-options.md.
-- CAVEAT: `runs` counts DISTINCT run_ids and `calls` counts rows, because they differ: thread_synthesis
--   and thread_audit append one row PER CONTINUING THREAD per run (~6x), so a naive COUNT(*) labelled
--   "runs" understates their per-run spend by that factor. The avg_* columns are per CALL.
--   `effort` renders NULL as "(not recorded)", never as a default -- a fabricated value would be
--   indistinguishable from a measured one, which is the whole point of the column. A DELIBERATE
--   SDK-default (cluster_extractjoin._thinking_for's next-gen branch resolves thinking to None on
--   purpose) is recorded as the distinct token "(sdk default)".
-- CAVEAT: `thinking` and `effort` arrived with migration 20260830210000, so every earlier row is
--   NULL -- which reads as "not recorded", NOT as "disabled". Do not treat the historical NULLs as
--   a measurement; the pre-migration configuration has to be read from the git history of
--   orchestrate.py and the agent frontmatter instead. A stage that changes model AND config in one
--   deploy shows as one row per combination, which is the point: the pairing is what you want to
--   see, since 5856f35 changed COHERENCE's model and prompt together and left them unseparable.

SELECT
  u.subagent,
  u.model,
  COALESCE(u.thinking, '(not recorded)') AS thinking,
  COALESCE(u.effort, '(not recorded)')   AS effort,
  COUNT(DISTINCT u.run_id)                AS runs,
  COUNT(*)                                AS calls,
  MIN(u.run_id)                           AS first_run,
  MAX(u.run_id)                           AS last_run,
  ROUND(AVG(u.api_cost_usd), 4)           AS avg_usd,
  ROUND(AVG(u.cache_read_tokens), 0)      AS avg_cache_read,
  ROUND(AVG(u.output_tokens), 0)          AS avg_output,
  ROUND(AVG(u.duration_ms) / 1000.0, 1)   AS avg_secs,
  -- The signature that identified COHERENCE's pathology: a stage re-reading far more than it
  -- writes is substituting retrieval for reasoning, and is a candidate for adaptive thinking.
  -- COHERENCE ran at ~200x before the change; SELECT sits near 13x and gained nothing from it.
  ROUND(1.0 * AVG(u.cache_read_tokens) / NULLIF(AVG(u.output_tokens), 0), 1) AS reread_ratio
FROM run_usage u
WHERE u.run_id >= (SELECT MAX(id) - :runs + 1 FROM digest_runs)
GROUP BY u.subagent, u.model, u.thinking, u.effort
ORDER BY u.subagent, first_run;
