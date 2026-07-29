---
title: An audit record that stores the decision instead of what was decided between cannot audit that decision
date: 2026-07-28
category: best-practices
module: threads, run, db
problem_type: best_practice
severity: high
applies_when:
  - Adding a trace, audit log, or artifact for a model-made choice
  - Asking "why did the pipeline pick this?" about a past run
  - A reviewer says a defect is "not detectable from the archive"
  - State the record captures gets overwritten in place on the next run
tags: [observability, audit, traces, model-decisions, provenance, run-artifacts]
---

The Haiku thread linker picks which ongoing thread each of today's stories continues. Nothing
persisted its output, so a sweep of 42 archived runs could not answer "does the linker
mis-assign stories?" — not because the answer was no, but because the evidence was never kept.

The first fix recorded the decision: story index, label, proposed thread, outcome. It passed
1082 tests and an end-to-end wiring test asserting the row landed in `run_artifacts`.

It was theatre. An adversarial harness drove the real linker with an Iran story attached to the
Gaza thread and got:

```json
{"story_index": 0, "label": "Iran talks enter third day",
 "proposed_thread": 2, "refused": null, "outcome": "continued"}
```

**Byte-identical to a correct link.** "Story 0 continued thread 2" reads the same whether thread
2 was the right thread or a different story entirely. The record answered "what did we do?" when
the question was "was it right?".

## What was missing was free

The candidate set — every active thread with its recent arc — was already in scope, in a local
variable, at the moment of the decision. Recording it turns the same trace legible: an Iran story
filed under a Gaza thread, with the Iran thread sitting in the same record as an available
alternative a reader can see was passed over.

It also cannot be recovered later. `decay_threads` overwrites thread status in place and
`touch_thread` overwrites the label with the newly-linked story's label — so by the time anyone
asks, the candidate set as it existed at decision time is gone, and the mis-link has partly
erased its own evidence.

A second gap of the same kind: `link_threads` swallows its own failures and returns all-`None`,
which is indistinguishable from a genuinely all-new day. That ambiguity is what hid run 244's
total continuity loss. Proposed-vs-validated counts separate the two, and they were also already
computed — they just died at the `list[int | None]` boundary.

## The shape

**Ask what a reader would need to disagree with the decision, and record that.** A record of the
conclusion is only auditable when the conclusion is self-evidently checkable; for a judgment call
it never is. Concretely, for any model-made choice:

- the alternatives it was choosing between, as they looked at decision time
- whether the call itself was healthy, distinguished from a legitimate empty result
- and only then what it picked

The tell is that the test suite cannot express the failure. If you cannot write a failing test
that says "this record shows a wrong decision", the record cannot show one. Every unit test here
asserted the shape of a dict the same commit invented — see
[[a-test-helper-that-cannot-express-the-failure-is-not-coverage]].

Related: [[a-detector-nobody-reads-is-not-a-detector]] is the delivery half of this — that one is
a correct signal routed nowhere, this one is a delivered signal that carries the wrong content.
Both look like working features. See also
[[an-observability-write-can-blind-the-monitor-it-feeds]] and
[[a-per-run-label-is-not-a-key]].

## One more trap in the same change

The trace was nearly written as a file into `claude_input/`, following the established pattern.
But `archive_run_artifacts` sweeps that directory *before* the thread path runs, so the file
would never have been collected — and the gap would have looked exactly like a working feature.
**When adding to a collected directory, check the collector runs after the producer.** It landed
in a `finally` for a related reason: thread identity is committed as it goes, so a later failure
in synthesis would otherwise leave the threads written and the explanation discarded, losing the
trace on precisely the degraded runs most worth auditing.
