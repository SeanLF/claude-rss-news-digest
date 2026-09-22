# The recheck's own band, three passes (spec §8.2; plan A1 task 1 step 7), 2026-09-22

Command: `bin/eval-repair --recheck-runs 3 --today 2026-07-26` (the `coherence_faithful` fixture is run 245's,
dated 2026-07-26). Two repair runs, then the live coherence.md re-checked the five repaired stories three
times; a story is clean only if it passes every pass.

| pass | repair: error removed | shape bad | gutted substitutes | missing |
|---|---|---|---|---|
| run 0 | 5/5 | 0 | 0 | 0 |
| run 1 | 5/5 | 0 | 0 | 0 |

No-new-error re-check, coherence.md × 3, union: **5/5 pass** in every pass, so the recheck band on this fixture
is 0 drops wide. Preservation ratios 0.40 to 1.04 (idx 15 shrinks to 0.4 to 0.54 of its length across runs).
Log: `data/recheck-band.log` (not tracked).
