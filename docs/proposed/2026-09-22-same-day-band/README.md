# The old system's same-day curation band: run 300, three reps (spec §7.4, §8.3)

Command: `bin/rerun-run --run 300 --reps 3` on 2026-09-22 (band dir `data/rerun-run/run300/20260922T002254Z`,
one container per rep, each with a fresh `/app/data/claude_input`). The span is `orchestrate_selections`
(CLUSTER, RECAP, SELECT, WRITE fan-out, PREHEADER, COHERENCE, repair + recheck) then `merge.assemble_selections`,
with the run's date pinned to 2026-09-18. Model calls on the subscription; costs are API-equivalent.

| rep | cost USD | wall s | stories shipped |
|---|---|---|---|
| 0 | 6.08 | 1035 | 16 |
| 1 | 4.36 | 779 | 16 |
| 2 | 4.89 | 850 | 22 |

Run 300 itself, from `run_usage` (curation stages only, threads excluded): $4.54 and 788 s of model
time; the whole run took 1020 s wall (10:25:40 to 10:42:40 UTC), so the non-model remainder (fetch,
fulltext, threads, render, email, archive) was about 230 s that day, with 659 articles clustered and
26 of 41 fulltext fetches extracted.

What the band says: three same-day re-runs of one closed day span $4.36 to $6.08 and 779 to 1035 s
for the curation span, and 16 to 22 shipped stories. The new system's same span (workflow time minus
time parked on signals) must sit inside that spread on the same input day, n ≥ 3, before the gate
counts it; a point comparison against one run would be inside the noise. The story count moving by 6
between reps with identical inputs is the SELECT instability already on record (Jaccard 0.24-0.34).

Not in this band: the recheck's own band (plan A1 task 1 step 7, `bin/eval-repair --recheck-runs 3`), still owed.

## The new system on the same day (2026-09-22)

`make band` (promptfoo `--repeat 3`, `digest/gate/band.yaml`): the TypeScript workflow on local Temporal, every
curation artifact regenerated (`--resume 300 --force`), approval sent at start so the hold is not counted.
Cost is the rep's `run_usage` rows; latency is start to result. Raw results: `new-system-promptfoo.json`.

| system | cost USD | wall s | stories |
|---|---|---|---|
| old (Python), 3 reps | 6.08, 4.36, 4.89 | 1035, 779, 850 | 16, 16, 22 |
| new (TypeScript), 3 reps | 3.96, 4.88, 4.11 | 710, 834, 732 | 17, 17, 16 |

Every new rep sits inside the old band's ceiling on cost and time (promptfoo's `cost` and `latency`
assertions, thresholds $6.08 and 1035 s, passed 3/3). The new band is lower and tighter on both, from one
day and three reps each; that is the §7.4 speed-and-cost criterion only, not the gate, which also needs
the planted-defect recall on the new runner and the two judges on three days.

An earlier hand-rolled attempt (now deleted) completed one rep at $4.14 and 579 s before its worker was
OOM-killed at a 512 MiB cap; the worker now runs at 2 GiB with a restart policy.
