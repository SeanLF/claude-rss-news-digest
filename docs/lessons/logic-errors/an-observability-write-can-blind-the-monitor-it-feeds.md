---
title: A best-effort write plus a blanket error handler can disable every invariant, including the ones that already worked
date: 2026-07-28
category: logic-errors
module: db, run_health, cluster_extractjoin
problem_type: logic_error
severity: high
applies_when:
  - A monitor reads a value parsed out of a file or blob (json_extract, JSON.parse, YAML load)
  - A "this must never break the main job" write is added on a critical path
  - A read helper returns {} / null on error and the caller treats that as "cannot judge"
tags: [observability, error-handling, json, sqlite, fail-open, monitoring]
---

To let `run_health` see degraded clustering, the extract-join stage writes `cluster_health.json`
and `db.get_run_health` reads a count out of it:

```sql
(SELECT json_extract(content, '$.batches_lost')
   FROM run_artifacts WHERE run_id = :r AND artifact_name = 'cluster_health.json')
```

The write is deliberately best-effort — observability must never cost the digest — so it catches
`OSError` and logs a warning.

Those two decisions combine badly.

## The chain

`json_extract` does not return NULL on unparseable input. **It raises**
`sqlite3.OperationalError: malformed JSON`. That raise is caught by `get_run_health`'s blanket
`except sqlite3.Error`, which returns `{}`. The caller treats `{}` as "cannot judge" and returns
before evaluating anything.

So a half-written health file silently disables **every** invariant — `ZERO_STORIES`,
`ZERO_RECIPIENTS`, `NO_USAGE_RECORDED`, `NO_ARTIFACTS`. A run that shipped nothing to nobody
would alert on nothing.

`Path.write_text` truncates before writing, so an ENOSPC or EIO partway through leaves exactly
the truncated file that triggers this. The guard that exists to protect the digest is what
creates the input that blinds the monitor.

```
truncated  json_extract      -> RAISES OperationalError -> {} -> ALL rules skipped
truncated  json_valid guard  -> None                    -> that one rule reads "cannot judge"
```

## The rule

**A parse failure on one optional field must degrade that field, never the whole read.** In
SQLite: `CASE WHEN json_valid(content) THEN json_extract(...) END`. Elsewhere: parse in the host
language where a `ValueError` can be caught around the single field.

## The generalisation worth carrying

An observability addition is not automatically safe because it "only reads". Ask what its failure
does to the reader — and specifically whether the reader's error path is *wider* than the thing
that failed. A blanket `except` around a multi-value read turns any one bad value into total
blindness, and blindness in a monitor is silent by construction.
