---
title: A repair rewrites the history a detector is backtested against, so validate on the recorded verdict rather than on current state
date: 2026-07-26
category: best-practices
module: run_health, threads, db
problem_type: best_practice
severity: high
applies_when:
  - Backtesting a new alert or detector against historical rows
  - Writing a detector for an incident whose data has already been remediated
  - Choosing between a stored signal and one derived from current state
tags: [backtest, detector, validation, data-repair, threads, matched_score, silent-failure]
---

## The lesson

Fixing the data destroys the evidence that the detector was supposed to find. If
a remediation ran between the incident and the backtest, the backtest measures
the remediation, not the detector -- and it fails *quietly*, reporting a clean
"no false positives" while also catching nothing.

The defence is to key the detector on **what the component recorded at the time**,
not on a value derived from the current state of the data. A recorded verdict is
immutable; a derived one moves whenever anything downstream rewrites a row.

## What happened

Run 244 lost all thread continuity: the linker answered correctly but wrote its
ids as JSON strings, and a strict `isinstance(int)` check dropped all 16 matches.
A new `NO_THREAD_CONTINUATIONS` invariant was written specifically to catch that
class, and defined a continuation structurally -- *an installment on a thread that
already appeared in an earlier run*.

Backtested over 41 runs, it fired on exactly two, both `status='failed'` and so
never reachable in production. That reads as a well-calibrated rule with zero
false positives. It was actually a rule with **zero true positives**: run 244
scored 5 continuations and did not fire.

The reason was `bin/repair-threads`, run hours earlier to merge five duplicate
threads created by the very same bug. The merge reassigned those installments
onto older threads, so the structural definition retroactively counted them as
continuations. The incident had been erased from its own audit trail.

`thread_installments.matched_score` had recorded the truth all along -- NULL for a
new thread, 1.0 for a linker continuation -- and all 16 of run 244's installments
were NULL. Switching the count to `matched_score IS NOT NULL` changed the backtest
from 0 true positives to:

| definition | fires on run 244 (`completed`) | false positives / 39 successful runs |
|---|---|---|
| derived (thread has an earlier run) | no | 0 |
| recorded (`matched_score`) | **yes** | 0 |

Both definitions are identical at run time -- at the moment run 244 finished, those
threads were newly created and both would have read zero. They differ only *after*
a repair, which is exactly when the backtest runs.

## Guidance

- Before trusting a backtest, ask **what has written to these rows since the
  incident**. Migrations, merges, backfills and repair scripts all count.
- A backtest that fires on nothing is not evidence of precision. Confirm it fires
  on the known-bad case first; a detector that catches its own motivating
  incident is the minimum bar, and "0 alerts" and "0 detections" look identical
  in a summary.
- Prefer a **recorded** signal to a **derived** one when writing a detector:
  what did the component itself say at the time, not what does the data now imply.
  Store the verdict if it is not already stored.
- When a repair is part of closing an incident, snapshot the pre-repair state
  first. It is the only validation set that still contains the failure.

## Generalisation

Any remediation that makes production correct also makes the corpus unrepresentative.
This is the same shape as fixing a bug before writing its regression test: the test
then passes for the wrong reason. It bites hardest when remediation and detection
are built in the same session, because the fix naturally lands first -- and the
detector is then validated against a world where the bug no longer exists.

## Related

- [[strict-types-on-model-output-turn-drift-into-silent-loss]] -- the run 244 bug
  this detector was written to catch.
- [[verify-the-validation-run-contains-the-code-under-test]] -- the sibling
  hazard: the data is absent rather than rewritten.
- [[test-the-detectors-not-the-happy-path]] -- the same instinct applied to tests.
- [[measure-a-prompt-change-against-a-control-run]] -- why a clean-looking result
  needs a control arm before it means anything.
