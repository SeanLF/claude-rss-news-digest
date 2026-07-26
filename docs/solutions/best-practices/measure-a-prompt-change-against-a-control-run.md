---
title: Measure a prompt change against a control run, not against history
date: 2026-07-26
category: best-practices
module: write, select, coherence, repair, orchestrate
problem_type: best_practice
severity: high
applies_when:
  - About to claim a prompt or context change improved anything
  - Comparing a new run against an archived production run
  - Verifying that a new input file reaches a model stage
  - Any A/B where one arm is history and the other is a fresh run
tags: [evaluation, control, variance, prompts, verification, null-result]
---

I added a context file so WRITE could see the headlines readers were recently shown,
verified it end-to-end, and said so: on all four in-window census days the exact
restated headline *would have been in front of WRITE*. That sentence is true. It is
also not evidence the fix works.

**It verifies delivery. It says nothing about effect.**

When effect was finally measured, there wasn't one.

## The measurement

Replaying run 237 (a known re-ship) against identical archived inputs, scoring each
headline's max TF-IDF similarity against the prior 7 days of shipped headlines:

| arm | mean | near-dups ≥0.75 |
|---|---|---|
| archived (production, no file) | 0.390 | 1 |
| **control** (replay, no file) | 0.359 | 2 |
| **treatment** (replay, with file) | 0.385 | 2 |

The treatment lands **between two runs of the same no-file configuration**. On the
specific story the change was built for, the control improved from 0.934 to 0.864
*without the file at all*; the treatment reached 0.803. Both still near-duplicates.

Without the control arm this reads as "0.934 → 0.803, a big improvement." With it,
the honest reading is "no measurable effect, and run-to-run variance is larger than
anything we did."

## The rule

**A before/after against a single archived run is not a measurement when the stage is
stochastic.** The comparison has three arms, not two:

1. history (what production did)
2. **control** — a fresh run at the *current* configuration, same inputs
3. treatment — a fresh run with the change

Arms 1 and 2 differ by run-to-run variance alone. If your effect is not clearly
larger than that gap, you have not measured anything. Every model stage here is
stochastic, so this applies to any change to `write.md`, `select.md`, `coherence.md`,
`repair.md`, or the files fed to them.

The control costs exactly one more run. Ours was ~$0.70 against a ~$3.43 pipeline —
cheaper than shipping a change believing it works.

## Two traps in the same experiment

**Pairing by index is not pairing.** Comparing `archived[i]` to `replay[i]` assumes the
stage emits stories in a stable order. It does not. That produced a first pass showing
"6 improved, 4 worse" that was pure noise from mismatched rows. Pair on something the
stage cannot reorder (cited article ids), or compare distributions and skip pairing
entirely — but do not silently assume order.

**n is smaller than it looks.** 16 headlines from 1 run per arm is one draw, not
sixteen samples; the headlines within a run are correlated through a shared model
call. Our result is "no positive signal," not "proven no effect" — but the direction
of the error matters: an underpowered experiment cannot rescue a claim, only fail to
refute it.

## Why the fix failed, which is the more useful half

The prior headline tells the model what **not** to write. It does not tell it what
**to** write. Given "don't resemble this" and nothing else, a model rewords —
"named" → "confirmed", "will become" → "to be sworn in" — which is precisely what the
prompt forbade. The facts that would have moved the angle existed (7 of them) but sat
in `whats_new`, computed after WRITE runs.

So the diagnosis from
[[ask-whether-the-signal-is-missing-or-merely-undelivered]] held — the signal existed
and wasn't delivered — but the wrong payload got transported. Delivering the right one
needs the ordering change that was deferred as unjustified. **Being right about the
mechanism does not make you right about the fix.**
