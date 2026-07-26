---
title: Ask whether the signal is missing or merely undelivered before scoping a detector
date: 2026-07-25
category: best-practices
module: write, threads, prepare, select
problem_type: best_practice
severity: high
applies_when:
  - A defect looks like "the pipeline cannot tell X from Y"
  - About to price, prototype, or research a classifier / scorer / detector
  - A stage produces a poor output and the obvious fix is a smarter model
  - Prior-art research returns accuracy ceilings that make a plan look marginal
tags: [architecture, ordering, detection, novelty, cost, prior-art, poc, scoping]
---

A story shipped, then shipped again days later under a headline that restated the
first one, stamped "Ongoing · day 3". The obvious reading: the pipeline cannot tell
a genuine development from a rehash. That framing led to a plan to compute a
per-cluster novelty signal and feed it to SELECT.

**The delta was never missing.** For the census cases that were thread
continuations, `thread_installments.whats_new` held **7 facts** (Burnham) and
**5** (Spain wildfire) at the exact moment the stale headline was written —
already synthesized, already audited fact-by-fact against sources, already
persisted. WRITE simply never received them, because `run.py` processes threads
inside `_archive_run_and_threads` *after* assembly, so thread identity does not
exist while WRITE runs.

The fix was to pass the data to the stage that needed it. Not detection —
**transport**.

## Why the distinction is worth a deliberate check

Detection has accuracy ceilings. Transport does not.

The prior art on novelty detection is discouraging and well-measured: TREC Novelty
2004 scored **F ≈ 0.185** aggregate (best run 0.42) and the track was discontinued;
TAC Update Summarization managed Pyramid F **0.15–0.46**, scoring *lower* on the
update framing than on plain summarization; TAP-DLND reached **79.2%** on full
articles against fixed seeds, a much easier setting than ours. Allan, Lavrenko &
Jin's *"First Story Detection in TDT is Hard"* (CIKM 2000) is a formal negative
result: FSD inherits topic tracking's error rate, and reaching a modest operating
point would need tracking to improve **20-fold**.

Every one of those numbers bounds *computing* a novelty judgment. **None of them
applies to handing an already-computed one to a downstream stage.** Conflating the
two turned a $0.006/run prompt change into a research project for several rounds
of analysis.

## The check

Before scoping a detector, ask in order:

1. **Does the pipeline already compute this?** Grep for it. Ours did, under a
   different name, three stages later.
2. **If it exists, who can see it?** Trace the actual read path — the file list in
   the prompt, the function arguments, the columns selected. A value that exists in
   the database but is not in the stage's inputs is invisible, and no model upgrade
   fixes that.
3. **If it is computed too late, is the ordering essential or incidental?** Ours was
   incidental: thread linking needs `clusters.json` and `selected.json`, both of
   which exist before WRITE runs.
4. **Only then price the detector.**

## Corollary: date-bucket a defect census against your own deploy history

The 8-case census was attributed to the thread feature. **Four of the eight
predated it** — threads launched 2026-06-29 and those cases ran 2026-05-01 to
2026-06-10. A fifth was a linker miss (no delta computed at all) and a sixth had
synthesis not run. Only two were the defect as diagnosed.

A census spanning an architecture change measures more than one thing. Bucket the
cases by date against the deploy log before attributing them to a single cause, or
the fix gets sized against a population it does not address.

## Corollary: two honest measurements of "the same" defect can disagree entirely

TF-IDF headline similarity (≥0.75, cross-day) found 8 re-ships. `whats_new` fact
count (≤1) found 8 thin installments. **The two sets do not intersect.** They are
different failures wearing the same symptom: headlines that repeat while the delta
exists, versus deltas that come back empty while the headline moves on. Neither
measurement was wrong. Reconciling them is what produced the causal breakdown
above — a single metric would have hidden it.

See also [[write-the-cost-equation-before-scoping-an-optimization]] — same family:
work out what the change actually moves before building it.
