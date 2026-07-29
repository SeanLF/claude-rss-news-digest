---
title: An identifier that is reassigned every run is meaningless outside its own run, and code that widens its scope will look like it works
date: 2026-07-28
category: best-practices
module: threads, merge, prepare
problem_type: best_practice
severity: high
applies_when:
  - An id is assigned positionally or sequentially per batch, per run, or per session
  - You are about to union, join, or compare ids across runs
  - A grounding or matching rule needs "the set of valid ids"
tags: [identifiers, scope, per-run, grounding, false-positives, stability]
---

Article ids in this pipeline are `A1..A{n}`, assigned fresh each run by `prepare`. `A12` was a
Messi story in run 240 and a Refugee Convention story in run 247. Verified directly from two
runs' `article_index.json`.

That makes the id a **run-scoped label, not a key**. It identifies a row in one run's index and
nothing else. Any rule that collects ids across runs is not building a bigger evidence set; it
is accumulating an unrelated slice of the `A1..A650` space.

## How this shipped anyway

Open questions in the thread ledger needed a "which ids are real here?" set to detect leaked
citations. Per-installment scope caught 3 of the 4 known leaks — the miss was a question citing
a fact the faithfulness audit had dropped, taking its sources with it.

The obvious fix was to widen the scope to the whole thread. Measured, it looked excellent:

```
BEFORE 4 leaking -> AFTER 0 leaking
clean questions wrongly dropped: 0 of 925
```

Both numbers are real. Both are worthless. The corpus happened to contain almost no prose with
A-designators in it, so the false-positive rate the measurement reported was a property of the
sample, not of the rule. Adversarial review varied the one input the measurement had held
constant — thread age — and found the actual shape:

| installments | thread | ids accumulated | P(a real "A320"/"A7" is dropped) |
|---|---|---|---|
| 10 | 196 | 31 | 6% |
| 27 | 12 | 201 | 41% |
| 33 | 6 | 325 | 58% |

It was dropping *"How many died on the A7 highway?"* from a French wildfire thread.

## The rule

**Scope a per-run id to its run. There is no correct wider scope.** If a narrower scope is
incomplete, the fix is to make that run's set complete — here, persisting the pre-audit cited
ids with the installment — not to reach into other runs for more ids.

## The measurement trap underneath it

A false-positive rate measured on a corpus that does not contain the confusable input is not a
false-positive rate. Before trusting one, ask what the rate *depends on* and check that the
sample varies it. Thread age was the variable; the corpus had it fixed at "mostly young".

## Consequence for identifier design

A content-derived id (a truncated hash of the article URL) would be stable across runs and this
class of bug would not exist. That is a stronger argument for changing the id format than token
cost or lexical distinctiveness, which were the reasons being weighed at the time.
