---
title: A bound that guarded one call does not guard N of them; splitting a stage into a fan-out silently multiplies every ceiling it inherits
date: 2026-09-01
category: best-practices
module: orchestrate
problem_type: logic_error
severity: high
applies_when:
  - splitting one model call into a fan-out of per-item calls
  - reusing a stage runner (run_stage, a retry helper, a budget wrapper) inside a loop
  - reviewing a change that says "each branch goes through the same machinery"
tags: [fan-out, concurrency, budget, timeout, run_usage, spend-cap, orchestrate, write]
---

# A bound that guarded one call does not guard N of them

*2026-09-01, building the per-story WRITE fan-out (1c6ff2b). The first working tree reused
`run_stage` verbatim per branch — which was the point, and which was also the defect. Review
caught it before the commit landed; what shipped re-derives every bound below. This records
the state the review saw, not the code on main.*

## What happened

WRITE was one SDK call over every selected story. Isolating each story to its own call
cut fabricated specifics sharply, so the fan-out was worth building. `run_stage` already
carried everything a stage needs — attempt timeout, spend cap, transient retry, usage
capture, validation, one clean-slate re-attempt — so each branch was routed through it
unchanged. "Every branch keeps the same bounds" reads like the safe version of the change.

It is the unsafe version. Every one of those bounds was sized for *the* stage, and the
fan-out turns one stage into up to twenty (`select.md`'s hard max, 6 + 14):

| Bound | Sized for one stage | Inherited by 20 branches |
|---|---|---|
| `_STAGE_BUDGET_USD` = $8 | trips before one stage outspends a whole run (~$5) | $160, 30x a normal run |
| `_STAGE_ATTEMPT_TIMEOUT_S` = 45 min | 2 attempts = 90 min | 5 waves x 2 attempts = 7.5h, past both the run budget and the systemd start-timeout |
| usage emitted on stage return | a stage that raises still records earlier stages | 19 paid branches discarded because the 20th failed |

None of these are visible in the diff. The fan-out reads as strictly-more-bounded than
the batch call it replaced: twenty capped calls instead of one. The multiplication only
appears if you write the arithmetic down.

The third row is the sharpest, because the codebase had *already* fixed it one level up.
`orchestrate_selections._record` exists, with a comment saying so, because a stage raising
used to discard every earlier stage's usage row. The fan-out reintroduced exactly that bug
inside the write phase, at 16x the granularity, while the fix that named it sat unchanged
three functions away.

## The rule

**When one call becomes N, re-derive every ceiling from N, and say what N's worst case is.**
Not "each branch is bounded" — the phase is what has to be bounded.

Concretely, for each inherited bound ask which of three it is:

- **Per-item, and correctly so** — a retry count, an idle timeout. Leave it.
- **Per-stage, and must be re-sized** — the attempt timeout. Give the branch its own,
  derived from what one item actually needs, and check the fan-out's worst case
  (`ceil(N / concurrency) * attempts * timeout`) against the enclosing budget. Derive `N`
  from a documented maximum, not from the run you happened to replay.
- **Per-stage, and must become a phase-level accumulator** — the spend cap. A per-item cap
  does not bound a phase; only a running total does.

And state the accumulator's real precision. A running sum read before an item starts is a
ceiling on *starting* items, not on total spend: at the moment it trips, `concurrency`
items are already in flight, each able to bill its own cap on each of its attempts. The
honest worst case is `phase_cap + concurrency * attempts * item_cap`. Write that in the
docstring rather than implying the cap is exact.

## Why the tests did not catch it

They did the opposite — they made it look handled. The first assertion drafted (never
committed) checked that every branch carried the stage-level `max_budget_usd`. It passed, it
was true, and it pinned precisely the bug: every branch carried the whole-stage cap. A test
can pin the mechanism faithfully and still say nothing about the quantity that matters. The
test that catches this asserts the **aggregate** — that the phase stops once the branches
have billed the cap (`test_write_per_story.py::TestFanOut`), and that a branch's cap is
smaller than the stage's.

The same shape applies to the usage row: `[r["subagent"] for r in rows] == ["write"]` on
the happy path proves the aggregation works and proves nothing about the failure path,
where the rows are actually at risk.

## What to do next time

- Before reviewing the fan-out's logic, list every constant it inherits and multiply each
  by N. If the product is not defensible, the bound is wrong even if the code is right.
- Assert aggregates, not per-item values, for anything that was a per-stage guarantee.
- Grep the surrounding module for a comment describing a bug you might be re-creating one
  level down. `_record`'s comment named this one in advance.

## See also

- [[a-deadline-on-the-waiter-does-not-bound-the-worker]] — the neighbouring failure: a bound
  that exists but measures the wrong thing.
- `newsroom/tests/test_write_per_story.py::TestFanOut` — the aggregate assertions.
