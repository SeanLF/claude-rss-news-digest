# THREADS in TypeScript: parity and a live run (2026-09-23)

Stale by default. What was measured when the threads activities replaced the stub.

## Deterministic parity: runs 300–304, equal

`bin/threads-oracle 300 301 302 303 304` rolls each run's thread tables back to just before it and runs
the Python linker, synthesis, audit and late binding from there, with the model calls answered from the
run's archive. `digest/src/threads/threads.parity.test.ts` runs the TypeScript from the same database
with the same answers. Everything came out equal on all five runs: every prompt (after the URL scrub),
`thread_assignments.json`, `thread_links.json`, `thread_installments.json`, the render contexts, and the
`threads`, `thread_installments`, `thread_questions` and `thread_runs` rows.

The rollback is faithful: on every run the replayed candidate set (45–50 threads with their arcs), the
story outcomes and the continued thread ids equal what production recorded in `thread_links.json`.

Negative controls: 19 single-line mutations (tie order, hub stripping, a stopword, the arc length, the
dormancy bound, the memory depth, the day count, citation order, each idempotency guard, fail-open, the
workflow's catches) each fail a test. One survived and is dead code in production: `SUMMARY_CHARS`
(400) never cuts, because prepare caps summaries at 200.

## Live, run 304: 16/16 stories agree, $0.44

The whole phase in the worker image against run 304's rolled-back state (`digest/src/cli/threads-live.ts`):

| | archive (Python) | TypeScript |
|---|---|---|
| continued / new | 10 / 6 | 10 / 6, same thread ids |
| synthesized | 9 | the same 9 threads |
| facts kept per thread | 7, 3, 14, 4, 1, 6, 3, 12, 7 | 5, 3, 10, 4, 1, 6, 3, 12, 11 |
| cost | not recorded for the linker | $0.44, 19 calls |

The linker's band decided one design question. With a schema-constrained answer, 4 of 4 TypeScript
samples linked 8 stories and never continued thread 926 (North Korea sanctions). The Python's free-text
linker linked 9, 9 and 10 in three live samples, 926 every time. Free-text TypeScript: 9, 10, 9, the
same set as the Python's. The linker answers in free text; synthesis and audit keep their schemas.

Live spend for the whole measurement: about $1.05.
