# INCOMPLETE sweep data — 2026-08-30, stopped by a session rate limit

Raw records and harnesses rescued from the gitignored `scratch/` tree when the session limit
terminated both sweeps mid-run. **Every conclusion below is provisional. Do not cite these as
settled results.** Settled findings live in `docs/2026-08-30-health-check-and-clustering-sota.md`.

## CLUSTER extraction — INCOMPLETE, do not conclude

`extract.jsonl` holds 59 usable per-call records of a planned ~408. Only the `sonnet-4-6` arms ran;
**there is no `sonnet-5` data at all**, so the model question is untouched.

```
arm             calls   mean out   MAX out   MAX % of 32k   think tok   dupes   miskey
s46/adaptive        8       6173      9170          28.7%        3526       0        0
s46/disabled       51       2297      2808           8.8%           0       0        0
```

An interim report quoted 21% of the ceiling; with more calls landed the worst observed is **28.7%**.
Adaptive produces **2.7x the output tokens** and costs roughly **2.6x per call** ($0.11 vs $0.043).

**The ceiling question is NOT settled.** n=8 adaptive calls is far too few to characterise a tail,
and the quantity that matters is the worst case, not the mean. Extraction quality is intact in both
arms (0 duplicates, 0 mis-keyed ids), so there is no quality problem motivating the change — which
makes 2.6x per call hard to justify even if the ceiling proves safe. **Finish the sweep before
touching this stage**, and note the original 2026-07 incident was a single ~460-article call, not a
40-article batch, so batching may have removed the failure mode for reasons unrelated to thinking.

## WRITE — INCOMPLETE and UNEVEN

`records_A1..A4.jsonl` hold 12 / 2 / 3 / 12 records across the four arms. The uneven split means
the arms are not comparable; A2 and A3 barely started. **No conclusion is available.** Fixtures
were built from runs 276 and 280 (two source runs, as designed). `run_write_matrix.py` and
`score_specifics.py` are the harness; `write.md.snapshot` / `coherence.md.snapshot` in the original
scratch dir pinned the prompts under test.

## SELECT / REPAIR / RECAP — these ARE settled

`select.jsonl` (48), `repair.jsonl` (72) and `recap.jsonl` (9) backed conclusions already written up
in the main doc: SELECT gains nothing from adaptive and costs 2.2x, REPAIR surfaced the
`_index_by_article_ids` last-write-wins defect, and RECAP's grader is mis-calibrated rather than its
output wrong. Retained here as the evidence behind those claims.

## To resume

`sweep_extract.py` + `sweep_common.py` are the extract harness. Both invoke through Docker the way
`bin/sdk-canary` does, because `claude -p` cannot run nested in a Claude Code session. Verify
`thinking_tokens` is 0 on disabled arms and non-zero on adaptive ones before trusting any rerun —
a whole earlier sweep was invalidated by that check being absent.
