# Day 305 (2026-09-23): production against the TypeScript pipeline, same inputs

`python/` is production's digest for 2026-09-23 (run 305, from the `digests` table) with its archived
inputs. `typescript/` is the TypeScript pipeline rerun on the same archived fetch (`--resume 305 --force`
on a migrated copy of the live clone, thread settings as production), passed through the same web-copy
strip. The article set is identical. The TypeScript side replaces URLs with `[link]` at the source, which
changes how the rows split across the five CSVs.

The TypeScript run survived a disk-full crash of the whole local stack mid-run: Temporal resumed it from
its last completed activity after the restart.

| | production | TypeScript |
|---|---|---|
| cost | $5.77 | $5.09 (includes extract batches rerun after the crash) |
| stories | 17 | 15 |
| COHERENCE flagged | 1 | 2 |
| story overlap (cited-source Jaccard ≥ 0.2) | | 12 of the TypeScript run's 15 |

The Python pipeline's own run-to-run SELECT overlap is 0.24-0.34 Jaccard. That makes 12 of 15 no evidence of drift.

## Judges (`make judges FIXTURE=day-305/<side>`), pass rate per criterion

| criterion | prod / Claude | prod / Codex | TS / Claude | TS / Codex |
|---|---|---|---|---|
| 1 supported | 0.70 | 0.40 | 0.60 | 0.40 |
| 2 bound correctly | 0.68 | 0.20 | 0.60 | 0.40 |
| 3 not stale | 1.00 | 0.72 | 1.00 | 0.80 |
| 4 earns its slot | 1.00 | 1.00 | 0.88 | 0.95 |
| 5 one event | 0.68 | 0.40 | 0.60 | 0.60 |
| 6 selection | 0.92 | 0.00 | 0.96 | 0.00 |
| 7 reads clean | 0.95 | 1.00 | 0.96 | 1.00 |
| **overall** | **0.846** | **0.531** | **0.800** | **0.593** |

The runs: 5 reps each, except TS/Codex at 4 (one hit the Codex plan's usage limit). Raw output:
`data/judges-day-305-{python,typescript}-*.json` (gitignored).

The families disagree on direction (Claude prefers production by 0.046, Codex prefers TypeScript by
0.062), and every per-criterion gap sits inside the judges' own self-agreement band (0.83-0.91 on day
300). Codex fails criterion 6 on every story of both digests, which is a judge artefact.

**Reading:** no detectable quality difference on this day. It is one day. The spec's gate needs three,
with planted defects alongside.
