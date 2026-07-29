---
title: When only survivors persist, an unfiltered sibling tells the generator from the validator
date: 2026-07-26
category: best-practices
module: thread_synthesis, coherence, repair, merge
problem_type: best_practice
severity: medium
applies_when:
  - A generate-then-validate stage produced nothing and you cannot tell which half failed
  - Only post-filter output is stored, so the pre-filter state is unrecoverable
  - About to replay an expensive stage just to find out what it emitted
tags: [diagnostics, observability, llm-judge, validation, thread-synthesis]
---

`thread_synthesis` generates "what's new today" as a list of facts, audits each against
its cited sources, and persists only the survivors
(`thread_synthesis.py:240-242`). Six installments came back with **zero** facts,
including a court upholding a conviction and a rising earthquake death toll — obviously
newsworthy events.

Zero survivors has two very different causes:

- the **generator** emitted nothing, or
- the **validator** rejected everything.

Only survivors are stored, so the record cannot distinguish them. The obvious next step
is to replay the stage and watch — an LLM call, fresh scaffolding, and a result that may
not reproduce because the stage is stochastic.

## The cheaper move

**Find a field that passes through the generator but NOT the validator, and check whether
it is also empty.**

Here the audit only ever touches `whats_new`. The same model call also emits
`new_questions` and `resolved`, and those are persisted unfiltered. So:

- generator failed → `new_questions` empty too
- validator over-rejected → `new_questions` survives, because nothing filters it

Across 214 continuation installments:

| whats_new | installments | with new_questions > 0 |
|---|---|---|
| 0 | 6 | **0 (0%)** |
| ≥1 | 208 | **208 (100%)** |

Perfect separation. Every installment with facts has questions; every installment
without facts has neither. **The generator is emitting empty installments; the validator
never sees anything to reject.** No replay, no model call, one query.

That inverted the working hypothesis — the auditor had been the prime suspect, and a
whole prior-art sweep had been commissioned on LLM-judge over-rejection. The evidence
pointed the other way, and the fix belongs in the synthesis prompt.

## The general shape

A filtering stage usually has co-outputs the filter does not touch. They are a free
control: same generator, same call, no filter. If the unfiltered sibling is also empty,
the failure is upstream of the filter.

Worth designing for deliberately — when adding a stage that drops content, either keep a
count of what it dropped, or make sure some co-output escapes it. `whats_new` had no
`kept N of M` log line, and only the accident that questions ride along unfiltered made
this answerable at all.

See also [[test-the-detectors-not-the-happy-path]] and
[[null-data-and-missing-schema-are-different-failures]] — same family: two distinct
causes collapsed into one indistinguishable observation.
