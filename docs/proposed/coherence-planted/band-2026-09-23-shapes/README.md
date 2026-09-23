# Planted band, 2026-09-23: checker shape on the TS runner

`make planted` (promptfoo 0.123.1, `digest/gate/planted.yaml`), 3 reps per shape, Sonnet 5 adaptive,
8 planted defects. Raw: `data/planted-20260923T015848Z.json` (gitignored).

| shape | recall | false drops | cost $ | wall s |
|---|---|---|---|---|
| inline-grep | 8, 8, 8 | 1, 3, 2 | 1.28, 1.09, 1.36 | 218, 235, 224 |
| read-loop | 8, 8, 8 | 1, 2, 1 | 0.86, 0.89, 0.82 | 227, 286, 187 |

inline-grep's previous 3-rep band on the same runner scored recall 6, 8, 8. The rule set in advance was
to match the old Read loop's band (recall 8/8 across 5 reps, 1-3 false drops). By that rule the Read loop
is the production default: it scored 3/3 and costs a third less. inline-grep missed once in 6 reps.
