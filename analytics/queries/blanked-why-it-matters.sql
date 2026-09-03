-- QUESTION: How many shipped stories went out with an empty why_it_matters, and is that
--   rate moving?
-- WHY: Blanking is the only quality degradation in this pipeline that SHIPS. When
--   COHERENCE fails a story on why_it_matters alone, merge.py keeps the story and empties
--   the field. Nothing is dropped, so `run-reliability` shows a clean run, the story count
--   is unchanged, `coherence-repair-yield` records it as `failed_but_shipped`, and every
--   existing health signal reads normal -- while the reader gets a story with its "why this
--   matters" section missing. Run 280 (2026-08-30) shipped 6 of 16 stories that way, 38% of
--   the digest with no run-health rule covering it. This query is the read-out: it reads the
--   archived selections.json, which is what the reader actually received, rather than
--   inferring from the coherence report.
-- CAVEAT: Reads `selections.json` from run_artifacts, so it only covers runs 204+ (older runs
--   have no artifact) and a fail-soft archive write shows as a missing run, not a zero. It counts
--   the SHIPPED state, so it cannot distinguish "repair was never attempted" from "repair was
--   attempted and failed" -- read it against `repair_calls` in coherence-repair-yield, or against
--   data/repair_log.jsonl, for that split.
-- CAVEAT: this is a slightly WIDER population than merge.py's own `blanked` counter.
--   SELECTIONS_SCHEMA permits an empty why_it_matters, so a WRITE that emits "" ships and is
--   counted here without ever passing through the blanking path. The two will not agree exactly:
--   this is the reader-facing number, merge's log is the mechanism's.
-- CAVEAT: why_it_matters became repairable on 2026-08-30; runs before that had NO repair path for
--   this field at all, so a non-zero rate there is expected -- it is the baseline this change is
--   measured against, not a regression.
-- CAVEAT: must_know only. Briefs never rendered the field, and from 2026-09-03 (run 286) WRITE
--   no longer produces it for should_know, so counting that tier would read every brief as
--   blanked. Runs before 286 therefore show a SMALLER population here than they shipped with the
--   field -- the reader-facing number was always the must_know one. db.get_run_health counts
--   the same way.

WITH shipped AS (
  SELECT
    ra.run_id,
    DATE(r.run_at) AS run_date,
    TRIM(COALESCE(j.value ->> '$.why_it_matters', '')) AS wim
  FROM run_artifacts ra
  JOIN digest_runs r ON r.id = ra.run_id
  JOIN json_each(json_extract(ra.content, '$.must_know')) AS j
  WHERE ra.artifact_name = 'selections.json'
),
per_run AS (
  SELECT
    run_id,
    run_date,
    COUNT(*) AS must_know_shipped,
    SUM(CASE WHEN wim = '' THEN 1 ELSE 0 END) AS blanked
  FROM shipped
  GROUP BY run_id, run_date
)
SELECT
  run_id,
  run_date,
  must_know_shipped,
  blanked,
  ROUND(100.0 * blanked / NULLIF(must_know_shipped, 0), 1) AS pct_of_must_know_blanked,
  ROUND(AVG(blanked) OVER (ORDER BY run_id ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2)
    AS blanked_7run_avg
FROM per_run
ORDER BY run_id DESC
LIMIT :runs;
