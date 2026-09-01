---
title: Adding a probe that covers an uncovered case moves the number; restating a rule the prompt already contains does not
date: 2026-08-31
category: best-practices
module: coherence
problem_type: best_practice
severity: medium
applies_when:
  - You are about to fix a detector miss by restating, expanding, or emphasising a rule the prompt already contains
  - A prompt audit hands you several hunks and you are tempted to ship them as one change
  - You want to predict whether a prompt edit is worth measuring before paying for the runs
tags: [prompt-engineering, coherence, eval, ab-test, sonnet-5, measurement, ablation]
---

# Coverage is a lever. Emphasis is not.

Two prompt hunks from the same audit, same day, same style, measured against COHERENCE's known
always-miss cases on the committed run-245 fixture. Three arms, `bin/eval-coherence`, live model
calls through the production SDK path.

| index | what it tests | probe covering it BEFORE | control | F2+F3 | **F3 only** |
|---|---|---|---|---|---|
| idx 0 | scope + event-participant | **none** | 0/4 | 5/6 | **4/5** |
| idx 4 | absence (tenure length in no source) | **none for entity-predicate** | 2/4 | 5/6 | **4/5** |
| idx 3 | quantifier, "most" vs sources' "some" | **probe 1, verbatim** | 0/4 | 0/6 | **0/5** |

False-drops **0/35 in every run of all three arms**. idx 0 across all F3-bearing arms: 9/11 vs
0/4 control, Fisher p = 0.011.

- **F3** widened probe 2 to SCOPE / TIME-WINDOW / EVENT-PARTICIPANT and lifted probe 3's
  entity-binding off the headline to every field. **Shipped.**
- **F2** rewrote probe 1 from "find the single least-supported specific" to "list EVERY specific".
  **Measured and rejected** — pinned out by a test so it does not get re-added.

## Why F2 could not have worked

Probe 1 **already contained idx 3's rule, verbatim**:

> if sources say "some" or "many" and the story says "most", that FAILS

The prompt named the error class and gave the exact quantifier pair. The case was still missed
0/27 across 27 archived runs and four models, and 0/11 across every arm here. **The model was
never missing the rule.** Restating it optimises the wrong term.

## The test to apply before paying for runs

Ask: **does any probe currently reach this case at all?**

- **Nothing reaches it** -> adding a probe is a real lever. Measure it.
- **A probe reaches it and it still misses** -> rewording that probe is not the fix. The failure is
  upstream of the instruction, and a stronger phrasing will produce a null at full price.

Second independent confirmation in this repo. `project_prompt_audit_2026_08_30` reached it first
on a different set with different models: *"naming an error class in the prompt does NOT fix it."*

## Ablate, or you will attribute the win to the wrong hunk

F2 and F3 were measured together first, because they arrived as one audit. That arm proved
*something* worked and could not say which half — and it produced a trap. idx 4 improved 2/4 -> 5/6,
and idx 4 is an ABSENCE case, which probe 1 explicitly covers. The obvious reading was "F2's
enumeration helps you notice a missing specific", and a lesson claiming exactly that was drafted
and nearly committed.

**The ablation refuted it.** F3 alone reproduced the idx 4 gain (4/5). The cause was F3's probe 3
rewrite: "Keir Starmer" is a named entity and "after barely two years in office" is a predicate no
cited source supports, so widening entity-binding to every field reaches idx 4 directly. Coverage
again — not emphasis.

The error was reasoning from **hunk labels** instead of from **what each hunk actually reaches**.
An ablation arm costs one run and converts a plausible mechanism story into a fact. Skipping it
would have shipped an inert hunk plus a lesson whose reasoning was backwards.

## Method notes worth reusing

- **The historical control was stronger than a fresh one** (0/27, four models) but was NOT pooled
  with the contemporaneous 4-run control — different models, thinking disabled. Pooling would have
  inflated significance. Quoted p-values are the contemporaneous comparison only.
- **Aim at the right fixture.** The held-out planted sets are SATURATED (`planted274` scores 8/8 on
  the shipped prompt) and can only detect regression. The always-miss cases are in the committed
  `newsroom/tests/fixtures/coherence_faithful` (run 245), which `bin/eval-coherence` uses by
  default. One smoke-test call revealed this; a misdirected full experiment would not have.
- **These eval containers buffer all output until the process exits.** An empty log means still
  running, not dead — check `docker ps`. Two jobs were nearly re-run on that misreading.
- **A one-off `wrote no coherence_report.json` did not reproduce in 16 further calls.** Transient,
  not a property of the prompt. Worth re-checking before shipping a prompt that shows it twice.
