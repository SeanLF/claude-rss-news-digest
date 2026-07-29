---
title: A run timestamp written before it is read makes the pipeline filter out all its own input
date: 2026-02-06
category: logic-errors
module: run, db, feeds
problem_type: logic_error
severity: medium
applies_when:
  - A pipeline records "now" and later asks "what changed since last time"
  - Re-running the digest on a day it has already run
  - Debugging a stage that returns zero items with no error
tags: [ordering, idempotency, state, re-run, feeds]
---

# A run timestamp written before it is read makes the pipeline filter out its own input

## The bug

`start_run()` inserts a row with `run_at = now()` **before** `get_last_run_time()`
is called. The feed filter then computes "articles newer than the last run" and
the last run is *this* run, timestamped seconds ago. Every article is older than
the cutoff. Zero articles pass, no error is raised.

The symptom is a same-day re-run that fetches nothing, which reads like a feed
problem and is not.

## Workarounds

- `--write-only` re-renders from existing selections without re-fetching.
- `--force` overrides the duplicate-run guard (which itself fails closed, so it
  is safe to leave on by default).

## The general shape

Any code that both **records** a watermark and **reads** it in the same
transaction has an ordering dependency that is invisible at the call site. The
write and the read look independent; the correctness depends entirely on which
runs first.

When you see a "what changed since X" filter, check where X is written relative
to where it is read. If the write happens first, the filter excludes everything
the run was supposed to process.

## Related

- [[null-data-and-missing-schema-are-different-failures]] -- same debugging
  discipline: establish which layer is actually wrong before changing anything.
