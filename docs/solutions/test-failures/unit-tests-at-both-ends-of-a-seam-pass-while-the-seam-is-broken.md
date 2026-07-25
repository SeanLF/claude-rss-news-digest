---
title: Unit tests at both ends of a seam pass while the seam is broken
date: 2026-07-25
category: test-failures
module: prepare, digest, render
problem_type: test_failure
severity: high
applies_when:
  - Adding a field that one module writes and another reads
  - A function rebuilds a dict from named keys rather than copying it
  - A feature's tests all construct their own inputs
tags: [testing, seams, contracts, article-index, silent-failure, no-op]
---

`wire_agency` was added to detect when an outlet republishes wire copy. `prepare.py`
computed it into `article_index.json`; `digest.py` read it in `_collapse_key`. Unit tests
covered the detector (13 parametrized cases, real feed strings, precision traps) and the
consumer (6 cases for the collapse behaviour). **841 tests green, full CI green, and the
feature did nothing at all in production.**

Between the writer and the reader sits `digest.resolve_source`, which does not copy the
metadata — it **rebuilds** it from a whitelist:

```python
return {
    "name": meta["name"], "url": meta["url"], "bias": meta["bias"],
    "source_id": meta["source_id"], "original_title": meta["original_title"],
    "wire": meta.get("wire", False),
}
```

`wire_agency` was not in that list, so it was silently dropped on every call.
`src.get("wire_agency")` was always `None`, `_collapse_key` always fell through to its
fallback, and the behaviour was byte-identical to before the change.

## Why the tests could not catch it

Every consumer test **hand-constructed** its inputs:

```python
sources = [{"name": "SCMP", "original_title": "...", "wire_agency": "agence france-presse"}]
out = collapse_reposts(sources)
```

That passes whether or not anything in the pipeline ever sets `wire_agency`. Both sides
were individually correct; only the join was broken, and nothing exercised the join. A
reviewer found it by driving `resolve_article_ids` end-to-end with a realistic
`article_index.json` — the one path no test covered.

## The rule

**When a field crosses a module boundary, write one test that goes through the boundary**,
using the real writer's output shape and the real reader's entry point. Not a unit test on
either side. For this codebase that means: write an `article_index.json`, call
`digest.resolve_article_ids`, and assert the field survives.

Mutation-check it, or the test is decoration: delete the fix, confirm the test fails,
restore it. That takes a minute and is the only evidence the test covers what you think.

## The deeper smell

A whitelist rebuild is a **contract that must be maintained by hand and fails silently when
it is not.** Every future field has the same trap waiting. Either pass the metadata through,
or make the shape explicit (a dataclass / TypedDict) so a dropped field is a type error
rather than a `None`.

Note the asymmetry that made this expensive: `resolve_source` wraps the rebuild in
`except KeyError`, which logs and drops the source — and if all sources drop, the whole
story drops. So the safe fix is `meta.get("wire_agency")`, never `meta["wire_agency"]`:
`--write-only` re-renders from a persisted index that may predate the field, and a bracket
lookup there would be a live crash path on the recovery route.

See also [[strict-types-on-model-output-turn-drift-into-silent-loss]] — the same run
produced both, and both are the same failure class: a value quietly not arriving, with
every component reporting success.
