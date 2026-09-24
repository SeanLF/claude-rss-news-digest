# Thread parity on Postgres: runs 300–304 (2026-09-23)

Stale by default. The first run of `digest/src/threads/threads.parity.test.ts` after the Postgres port
and the schema rename (44a8dfe). The oracle was regenerated from the 2026-09-23b prod clone.

```
DB=<prod clone> bin/threads-oracle 300 301 302 303 304
make threads-parity        # imports each case's pre.db fresh, then runs the test: 5 passed (5)
```

**Result: 5 of 5 cases equal.** Two differences came from the oracle's fixture, not from the TypeScript,
and were fixed in `newsroom/tools/threads_oracle.py`. No TypeScript bugs were found. The test maps the
remaining differences, all intended changes in the new model.

Negative control: after changing one `resolved_how` in run 300's expected rows, the test failed on that
field. The oracle was checked the same way: after the first fixture fix, all five `expected.json` files
were byte-identical to the first generation (`cmp`). The Python's output did not move.

## Fixed in the fixture

| Difference | Cause | Fix |
|---|---|---|
| `bin/import-legacy` refused every pre.db with "derived status is its stored status (897 broken)" | The rollback emptied `digests`, so no run was published and `thread_state` had no rows. It also kept runs after N, and stored the decay verdict as of N-1. | Keep the issues of runs before N, drop runs after N (with their `source_health` and `run_usage`), and store the decay verdict at the start of N, which run N's own `decay_threads` sets before it links. |
| Run N's new threads got ids 957… in the Python but 903… in the TypeScript | `sqlite_sequence` kept the high-water mark of the threads the rollback deleted. The import resets each identity to the highest id present plus one. | Set each `sqlite_sequence` back to its table's highest surviving id. The Python then allocates 903…, as the TypeScript does. |

The importer does not carry `sqlite_sequence`. For threads in production this is harmless today. Ids
378–383 are gone from the middle of the range, but only the top matters for reuse, and on the clone
`seq` = `max(id)` = 956. Thread ids are public (`/thread/N`), so a gap at the top would have mattered.
Check it before the cut-over import with
`sqlite3 <clone> "select seq, (select max(id) from threads) from sqlite_sequence where name='threads'"`.

## Intended changes, mapped by the test

- **Derived state.** `threads.label`, `last_run_id` and `status` are no longer stored. The test derives
  label and last run from `thread_updates`, and drops the Python's `status` and `slug`.
  `first_run_id` → `created_run_id`, `merged_into` → `merged_into_id`.
- **Publication-gated visibility.** `thread_state` counts only updates from `published_runs`. The test
  replays run N without publishing it. Python's post-run columns against `thread_state`, per case:

  | run | threads | run N unpublished: missing / label + last-run differ | run N published (issue row inserted, rolled back) |
  |---|---|---|---|
  | 300 | 907 | 10 / 7 | 0 differences, status included |
  | 301 | 919 | 12 / 4 | 0 |
  | 302 | 925 | 6 / 10 | 0 |
  | 303 | 934 | 9 / 6 | 0 |
  | 304 | 940 | 6 / 10 | 0 |

  While run N is unpublished, its new threads have no row, and threads it continued show their previous
  label and last run. Once it is published, the derived label, last run and status equal the Python's
  on all 4,625 rows. The test now asserts the published half: it inserts run N's issue and compares
  `thread_state`. Negative control: after changing one expected status from dormant to active, that
  assertion failed.
- **Resolutions as rows.** A resolved question is a `thread_question_resolutions` row. `resolved_how`
  → `answer`, and `status` is whether a resolution exists.
- **`is_continuation` for `matched_score`.** The Python stored 1.0 on a continued installment. The
  test maps the boolean back to 1.0.
- **No `thread_runs`.** The per-run counts live in the `thread_health.json` artifact, which the test
  does not compare against the Python.

## Not covered

- **Merges and abandoned-run retraction.** These cases have no merged thread, and no failed run with
  thread writes. The parity test never reaches `retractAbandoned`, `undoRun` or the `merged` branch of
  `thread_state`, before or after this change. Only the unit tests cover them.
- **Run N's own status.** Run N imports as completed and sent, with no issue. In production it would be
  running. Nothing on the thread path reads it today.
- **The dormancy boundary is covered.** Between 6 and 11 threads per case sit exactly at the bound. A
  copy of run 303 with `dormant_after` set to 4 failed the test on the link prompt, which gained the 8
  threads at that bound (from the review).
