---
title: Inlining a corpus to escape the tool loop cuts cost 55% and costs absence detection; the file handoff earns its price for a checker
date: 2026-08-31
category: best-practices
module: coherence, orchestrate
problem_type: performance
severity: high
applies_when:
  - You are about to convert a file-handoff stage to single-turn to cut cache-read cost
  - You are reasoning about whether the Read-tool handoff is vestigial now the parent is Python
  - A change trades a large measured cost saving against a quality signal you have not measured
tags: [cost, coherence, cache, single-turn, absence, eval, ab-test]
---

# The 73.7% plumbing figure is real. "Therefore inline it" does not follow.

Context plumbing is **73.7% of this pipeline's bill** (runs 274-280: cache write $19.95 + cache
read $6.24 of $35.29, reconciled to +0.8%). COHERENCE re-reads its ~82k-token corpus roughly 48
times per run on a fresh input of **41.7 tokens** -- everything arrives through the Read-tool loop
as cache. `cluster_extractjoin` does the same class of work single-turn at `cache_read=785` while
producing the MOST output of any stage. The contrast is in-repo and controlled.

So converting COHERENCE to single-turn looked obvious. It was measured, on the committed run-245
fixture, against the shipped multi-turn prompt.

| | multi-turn (n=5) | single-turn (n=8) |
|---|---|---|
| recall, mean of 6 | **4.60** | **3.50** |
| idx 4 (ABSENCE: tenure no source states) | 4/5 | **1/8**, p = 0.032 |
| idx 0 (positive mis-binding) | 4/5 | 4/8 |
| runs with a FALSE DROP | **0 of 15**, all arms | **2 of 8** |
| cost per call | $1.61 | **$0.72** |

**Rejected.** A 55% cut on the largest line item does not buy a checker that finds less and drops
correct stories. False drops are the failure this stage was reframed in 2026-07 to stop.

## The mechanism, and why it is specific rather than general

The damage is **shaped**, not diffuse. The positive mis-binding case held; the ABSENCE case
collapsed 4/5 -> 1/8.

With tools, "no cited source states this" is a **search that terminates**: the model issues
bounded Reads, gets bounded results, and exhausting them is a finite act it can complete. With
82k tokens sitting in one message, confirming a negative means scanning everything, and
satisficing is cheap and invisible.

That predicts the result: inlining helps *cross-referencing* (idx 0 got no worse and was cheaper)
and hurts *exhaustive negative checks*. It also predicts where the conversion IS safe -- a stage
whose job is generation or positive extraction, not absence detection. `cluster_extractjoin`
extracts entities that ARE present, which is why it works single-turn.

## What this settles about the file handoff

The handoff's stated rationale is void -- it was written when the parent was an LLM dispatcher,
and `orchestrate_selections` has been plain Python since 2026-06. But **void rationale does not
mean no value.** For COHERENCE the tool loop buys absence detection, and it costs $0.89/call to
buy it. That is now a priced, measured trade rather than an inherited default.

Do not generalise the rejection either. The plumbing cost is real and the lever still exists for
WRITE and repair_recheck, which are generation stages. Measure each one; the answer is per stage.

## Method notes

- The single-turn prompt was **derived** from the shipped one by swapping only the I/O section,
  with a test asserting the probe block survives byte for byte. Hand-writing a second prompt would
  have made this a quality experiment wearing a cost experiment's clothes.
- The first 3 runs showed the idx 4 regression at p = 0.14 -- consistent but underpowered. Going to
  n=8 was what made it significant AND what surfaced the false drops, which did not appear at all
  in the first three runs. A 19%-of-run prize deserves n>3 before you discard it.
- `$0.72` is the honest production figure, not the `$0.30` of runs 2+: those are within-container
  cache hits, and production runs a fresh container daily.
