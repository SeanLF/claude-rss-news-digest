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
