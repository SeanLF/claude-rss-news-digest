# THREADS in TypeScript: parity and a live run (2026-09-23)

Stale by default. What was measured when the threads activities replaced the stub.

## Deterministic parity: runs 300–304, equal

`bin/threads-oracle 300 301 302 303 304` rolls each run's thread tables back to just before it and runs
the Python linker, synthesis, audit and late binding from there, with the model calls answered from the
run's archive. `digest/src/threads/threads.parity.test.ts` runs the TypeScript from the same database
with the same answers, read as free text as production reads them. Everything came out equal on all
five runs: every prompt (after the URL scrub), `thread_assignments.json`, `thread_links.json`,
`thread_installments.json`, the render contexts, and the `threads`, `thread_installments`,
`thread_questions` and `thread_runs` rows. Without the oracle the test shows as a named skip that says
how to generate it.

The rollback is faithful: on every run the replayed candidate set (45–50 threads with their arcs), the
story outcomes and the continued thread ids equal what production recorded in `thread_links.json`.

Negative controls: single-line mutations of the ported logic (tie order, hub stripping, a stopword, the
arc length, the dormancy bound, the memory depth, the day count, citation order), of every guard
(each idempotency return, the undo steps of a forced re-run, the Python-resume return, the label check
in planning, non-object link entries, fail-open, health recompute, the audit-count floor, the phase
bound, the defaults and their validation) and of the workflow's catches each fail a test. Two
survivors, for the record:

- `SUMMARY_CHARS` (400) never cuts, because prepare caps summaries at 200. Dead in production.
- Removing the in-transaction recheck in `threadsLink` is caught only because the unique
  `(run_id, artifact_name)` index then makes the losing attempt's commit throw. The recheck is
  belt-and-braces over that index: without it a lost race costs a retry, never a duplicate thread.

## Live, run 304: 16/16 stories agree, $0.41

The whole phase in the worker image against run 304's rolled-back state (`digest/src/cli/threads-live.ts`),
all three calls free text as production's are:

| | archive (Python) | TypeScript |
|---|---|---|
| continued / new | 10 / 6 | 10 / 6, same thread ids |
| synthesized | 9 | the same 9 threads |
| facts kept, per thread (792, 889, 907, 913, 918, 925, 926, 928, 932) | 7, 3, 14, 4, 1, 6, 3, 12, 7 | 5, 3, 13, 4, 1, 4, 4, 12, 8 |
| facts kept, total | 57 | 54 (of 61 synthesized) |
| cost | the linker is not recorded | $0.41, 19 calls |

An earlier live run with schema-constrained synthesis and audit kept 5, 3, 10, 4, 1, 6, 3, 12, 11: the
two threads furthest from the archive (907: 14 → 10, 932: 7 → 11) came back to 13 and 8 in free text.

The linker's band decided the same question first. With a schema-constrained answer, 4 of 4 samples
linked 8 stories and never continued thread 926 (North Korea sanctions). The Python's free-text linker
linked 9, 9 and 10 in three live samples, 926 every time; free-text TypeScript linked 9, 10 and 9, the
Python's set.

Live spend for the whole measurement: about $1.50.
