---
title: Prefer a ground-truth check over a pattern, rather than reshaping the data so a pattern becomes safe
date: 2026-07-28
category: best-practices
module: prepare, threads, utils, merge
problem_type: best_practice
severity: medium
applies_when:
  - Proposing an identifier format change to make leaked ids detectable
  - Weighing a regex guard against a check that has the real answer available
  - About to redesign data so a detector can be more aggressive
  - Estimating a guard's false-positive rate without measuring the corpus
tags: [identifiers, guards, false-positives, measurement, regex, ground-truth]
---

Article ids are `A1..A{n}`. They leaked to readers three times, so the obvious fix was to make
them pattern-matchable: `art_316`, or `a_316`, or an 8-hex hash of the URL. A separator means no
natural-language collision, so a regex could strip a bare leaked id safely.

**Measuring the archive killed it.** Across runs 204–247:

| question | measured |
|---|---|
| does the model ever mangle an id it was given? | **0 / 2,235** stored references |
| bare-id leaks in WRITE's narrative fields | **0 real** in 2,037 strings |
| what the naive `\bA\d+\b` regex *did* match | 1 hit: **"partially closing the A6 motorway"** |
| leaks the shipped ground-truth guard misses | **1**, and it self-clears |

The single thing a bare-id pattern would have caught in 42 runs was a French motorway in a
wildfire story — it would have silently edited a reader-facing summary. Probes:
`scratch/2026-07-28-id-probes/`.

## Why the format change was the wrong end

The existing guard does not pattern-match. It uses the fact's own `sources` list as ground truth:
an id present in both the prose and the cited sources is the model citing itself, while `A19` in
"the A19 chip" is an Apple chip precisely because A19 is not cited. That has no false positives
by construction, and it needs no cooperation from the id format.

So the format change would have bought a *weaker* guard. Its only real gap was `new_questions`,
which carried no `sources` field — a missing-ground-truth problem, fixed by adding `cited_ids`,
not by reshaping every id in the pipeline.

Two further findings worth not re-deriving:

- **Cross-run stability was already available.** `run_artifacts` archives `article_index.json`
  per run, so 2,235 / 2,235 stored references resolve to a URL today. The join was a two-step
  lookup, not a missing capability. A content-derived id would have removed a footgun, not added
  information.
- **Its payoff surface is 2%.** Only 416 of 19,954 distinct URLs ever appear in more than one
  run, so a "stable across runs" id is the same value as a counter for 98% of articles.

## The shape

**When a guard wants the data reshaped, check whether the real answer is already in scope.** A
pattern guesses from the text; a ground-truth check consults what the system already knows. The
second is strictly better and usually already available — the cited sources, the index, the
candidate set.

And **measure a guard's false-positive rate on the actual corpus before adopting it.** The
motorway case was invisible to reasoning and obvious to one query. A false-positive rate measured
on a corpus lacking the confusable input is not a false-positive rate — see
[[a-per-run-label-is-not-a-key]], where the same mistake ran the other direction.

Related: [[a-lexical-detector-is-anti-correlated-with-a-rewording-defect]],
[[test-the-detectors-not-the-happy-path]],
[[an-audit-record-of-the-verdict-cannot-audit-the-verdict]].
