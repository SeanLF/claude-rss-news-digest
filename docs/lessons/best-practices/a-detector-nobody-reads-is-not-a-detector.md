---
title: A detector that computes the right answer into a place nobody looks has not detected anything
date: 2026-07-28
category: best-practices
module: eval_stages, cluster_extractjoin, run_health
problem_type: best_practice
severity: high
applies_when:
  - Adding a check, grader, or invariant
  - A defect is found that "we should have caught"
  - A signal exists only in a log line or a dev-only harness
tags: [observability, detection, alerting, graders, dead-code]
---

SELECT's `cluster_index` was wrong for 16% of entries across 30 archived runs — in run 247, 7 of
12 should_know entries pointed at a cluster containing none of their own articles. That broke the
join to thread context, so those stories silently got no "Ongoing" continuity.

`eval_stages.py` had been computing `sel_ids - cluster_ids` and failing on stray ids the entire
time, under the check name `select_article_ids_in_cluster`.

Ten affected runs. Nobody saw it.

## Why

`eval_stages` runs in the dev eval harness. Nothing in the production path consults it, and
nothing routes its failures anywhere a person encounters them. The computation was correct and
the delivery was absent, which is indistinguishable from having no check at all.

The same shape appeared twice more the same day:

- The extract-join stage logged `40/688 articles title-only fallback (degraded clustering)` at
  ERROR for 7 of 40 runs. The log is a 100 KB rotating file. Every one of those runs exited 0.
- `run_health.py` — a module built specifically for "the run completed but is silently wrong" —
  could not see either defect, because both signals lived only in log lines.

## The rule

**When adding a check, name where its output lands and who reads it.** If the answer is "a log
file" or "a harness we run manually", it is not yet a detector — finish it by routing the signal
somewhere consulted: a persisted artifact an invariant can read, an alert, a non-zero exit on a
gate that actually runs.

## The corollary that is easy to miss

When a defect gets through, check whether something already computed it before writing a new
check. Adding a second detector next to an unread first one doubles the code and changes
nothing. The fix for `cluster_index` was not a new check — it was deleting the reliance on a
model-counted position, plus wiring the *existing* signal somewhere visible.

## Update (2026-09-01)

`select_article_ids_in_cluster` (the `sel_ids - cluster_ids` computation this lesson is about)
was deleted, not fixed: `b114c6a` (2026-07-28, the same day as this lesson) retired
`cluster_index` as load-bearing pipeline-wide, so by 2026-09-01 the check was failing 52% of
healthy runs for a reason unrelated to id integrity — 88.5% of its failures were the model
miscounting a position in a several-hundred-element array, not a stray id. See
`docs/2026-09-01-eval-stages-grader-diagnosis.md`.

The corollary above still held: rather than leave SELECT with no id-integrity check,
`grade_select` now asserts `select_article_ids_resolve` (every `article_id` resolves in
`article_index.json`) — the same pattern already used by CLUSTER (`cluster_ids_in_index`) and
WRITE (`write_source_ids_in_index`), and the assertion the pipeline actually depends on.
