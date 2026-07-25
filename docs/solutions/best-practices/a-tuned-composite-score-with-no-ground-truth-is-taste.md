---
title: A tuned composite score with no ground truth is taste wearing a number
date: 2026-07-22
category: best-practices
module: eval, select, coherence
problem_type: best_practice
severity: high
applies_when:
  - Combining several signals into a single score, index, or ranking
  - Choosing coefficients or weights by judgment
  - About to version a scoring formula (v2, v3, ...)
  - Evaluating someone else's "index" before trusting it
tags: [eval, metrics, scoring, ill-posed, ground-truth, calibration]
---

# A tuned composite score with no ground truth is taste wearing a number

## The rule

When you sum weighted signals into one number and no held-out ground truth
exists, the weights are unfalsifiable. There is no experiment that returns "0.25
was wrong, use 0.30." Successive versions of the formula are not convergence,
they are refitting to intuition while gaining the appearance of rigour.

Before shipping a composite score, answer:

1. **What would falsify a weight?** If nothing would, the number is a
   presentation choice, so present it as one.
2. **Are the summed quantities commensurable?** If a magnitude-6 earthquake and
   an active missile barrage both add 25 to the same index, the index is
   asserting they are equivalent. That assertion needs defending.
3. **What is the self-agreement band of the reference?** For model-generated
   gold, measure the N-run band before trusting any metric computed against it
   (see the Sonnet-vs-Sonnet ARI 0.60-0.88 finding in
   `docs/2026-06-26-cluster-eval-methodology.md`).

## What survives the test

Categorical, sourced, auditable rules survive; continuous tuned blends do not.
A rule of the form "UCDP classifies this as an active war, therefore the floor
is 70" is checkable against a citable source and wrong in a way someone can
demonstrate. The smooth score layered on top of it is decoration.

Prefer, in order:

1. A **decision rule with a citable trigger** ("N independent sources corroborate").
2. A **rank** over an absolute score, when only relative ordering is used.
3. A **score with a published calibration** against held-out outcomes.

Reach for a weighted composite only when none of the above is available, and
label it as editorial rather than measured.

## Worked example

WorldMonitor's Country Instability Index v8 computes
`baseline*0.40 + (Unrest*0.25 + Conflict*0.30 + Security*0.20 + Information*0.25)*0.60`
plus ten supplemental boosts (earthquakes up to +25, sanctions +14, wildfires
+8, ...), clamped by UCDP conflict floors and State Department advisory floors.

The **inputs** are excellent and mostly primary (ACLED, UCDP, USGS, NASA FIRMS,
Cloudflare Radar). The **floors** are the defensible part: categorical,
sourced, auditable. The **weighted blend and boost table** have no ground truth,
no published calibration, and no reported skill against a baseline. Version 8
means eight rounds of tuning to taste.

Useful as attention triage ("where should I look today"). Not usable as a risk
estimate. That distinction is the whole lesson.

## Related

- [[write-the-cost-equation-before-scoping-an-optimization]] -- same discipline
  applied to cost: write the model down before spending effort against it.
- `docs/2026-06-26-cluster-eval-noground-truth-literature.md` -- task-grounded
  evaluation beats ARI; stability is not validity.
