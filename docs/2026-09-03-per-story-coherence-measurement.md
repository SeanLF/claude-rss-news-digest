# Per-story single-turn COHERENCE, measured against the tool loop

*2026-09-03. Phase 1 of `docs/2026-09-03-stage-invocation-rewrite-plan.md`. Harness:
`bin/eval-coherence --runs 5` and `bin/eval-coherence --runs 5 --per-story` (499dfd8, a777a29),
on the run-245 labelled fixture (`newsroom/tests/fixtures/coherence_faithful/`: 17 stories,
6 hard positives, 8 borderline, 35 clean fields). Model `claude-sonnet-5`, thinking adaptive,
both arms, same day, same container.*

## Verdict

**Gate failed on precision. COHERENCE stays on the tool loop.** The absence-detection
mechanism the 2026-08-31 lesson predicted is confirmed at per-story size, and the per-story
arm is not less sensitive than production. It is more strict on labelled-clean fields, and
under this eval's own rules a flag on a clean field is the failure the stage exists to avoid.

Phase A therefore applies to the generation stages (WRITE, PREHEADER, RECAP, repair) pending
Phase 2, and not to COHERENCE or the repair re-check.

## Numbers

| | multi-turn (n=5, production today) | per-story single-turn (n=5) | 2026-08-31 whole-corpus single-turn (n=8) |
|---|---|---|---|
| recall, mean of 6 | **4.8** (5,5,5,5,4) | **5.0** (5,5,5,5,5) | 3.50 |
| idx 4, the absence case | 5/5 | **5/5** | 1/8 |
| idx 3, "most" vs "some" headline | 0/5 | 0/5 | (not reported) |
| runs with a false drop | **0 of 5** | **4 of 5** | 2 of 8 |
| false-dropped fields, total | 0 | 7 over 35 clean fields x 5 runs | |
| borderline flagged | 0-1 per run | 0-2 per run | |
| cost per run, API-equivalent | ~$1.61 (lesson figure; this arm prints no usage line) | **$0.62** fresh container, $0.30 with the container's cache warm | $0.72 |
| output tokens per run | not printed | 21-23k across 17 calls | |

The idx 3 miss is the known one: 0 for 50 across model, thinking, prompt and effort in the
prompt audit. Nothing here changes it.

## What the false drops were

Fields the labels mark clean that the per-story checker flagged, with the checker's reasons
from the last run (earlier runs' reports were overwritten; see the harness note):

| idx | field | runs flagged | checker's reason (run 4) | label |
|---|---|---|---|---|
| 16 France heatwave / social-media ban | headline, summary | 3 | not captured (runs 1-3) | clean |
| 7 Philippine sailor | summary | 2 | "calls the injured person a 'Philippine marine' but cited sources describe him as a 'sailor'/'soldier'" | clean; the label's own borderline note says sources say **marine** and the headline's "sailor" is the inconsistency |
| 2 Zelensky protests | summary | 1 | "no cited source states the protests reached their fifth day specifically 'on Tuesday'" | clean |

Two readings, and the second one is a question for the label set rather than the checker:

- The per-story checker, with nothing to read but the cited sources, scrutinises harder. Its
  flags are precision-level (a weekday, a rank term), not invented. In production every one
  of these would go to repair-not-drop and cost a repair call plus a possible needless edit,
  not a lost story. That is a different cost from the drops the 2026-07 reframe was about.
- idx 7 is a direct contradiction between the checker and the label about what A17/A278 say.
  The label was blind re-audited (v2 fixed "7/sailor-marine"), so it stands as ground truth
  here, but it is the cheapest of the seven to re-read.

Neither reading changes the verdict under the plan's gate ("0 false drops in 5 runs"). Both
are worth carrying into the Phase A plan: if per-story checking is ever revisited, the gate
should be stated in production terms (flags that repair cleanly versus flags that drop) and
the label set re-read on idx 7 and idx 2 first.

## What this settles

- The lesson's mechanism holds and is now bounded on both sides. Whole-corpus inline: absence
  detection collapses (1/8). Per-story inline: absence detection intact (5/5). The variable
  was the size of the negative the model had to exhaust, as the lesson said.
- Recall is not the cost of single-turn at this size. Precision is. A checker that sees less
  context flags more, and the labels call some of that over-flagging.
- The saving is real and smaller than it looks: $0.62 against ~$1.61 per run, once per day,
  before repair-not-drop absorbs the extra flags.

## Harness note

`eval_coherence.py` writes every run's report to the same `coherence_report.json`, so only
the last run's reasons survive. The next harness change should keep `coherence_report.<n>.json`
per run; the three idx 16 flags above are the evidence that was lost to it.

## Raw scorecards

### multi-turn (production delivery)

```
COHERENCE eval  model=claude-sonnet-5  thinking=adaptive  runs=5  fixtures=eval-fixtures
  labels: 6 hard, 8 borderline, 35 clean
  run 0: recall 5/6  false-drops 0/35  borderline 1/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
  run 1: recall 5/6  false-drops 0/35  borderline 0/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
  run 2: recall 5/6  false-drops 0/35  borderline 0/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
  run 3: recall 5/6  false-drops 0/35  borderline 1/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
  run 4: recall 4/6  false-drops 0/35  borderline 0/8  unmapped 0  malformed 0
          missed: [(3, 'headline'), (15, 'summary')]
  best recall 5/6  worst false-drops 0
  OK (no egregious regression; review the scorecard for recall changes)
```

### per-story single-turn

```
COHERENCE eval  model=claude-sonnet-5  thinking=adaptive  runs=5  fixtures=eval-fixtures
  labels: 6 hard, 8 borderline, 35 clean
  [per-story x17] input=34 cache_write=79289 cache_read=30485 output=22984 cost=$0.6171
  run 0: recall 5/6  false-drops 0/35  borderline 1/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
  [per-story x17] input=34 cache_write=0 cache_read=109774 output=20712 cost=$0.2930
  run 1: recall 5/6  false-drops 1/35  borderline 1/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
          FALSE DROPS: [(16, 'headline')]
  [per-story x17] input=34 cache_write=0 cache_read=109774 output=21850 cost=$0.3044
  run 2: recall 5/6  false-drops 2/35  borderline 0/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
          FALSE DROPS: [(16, 'headline'), (16, 'summary')]
  [per-story x17] input=34 cache_write=0 cache_read=109774 output=22445 cost=$0.3103
  run 3: recall 5/6  false-drops 2/35  borderline 1/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
          FALSE DROPS: [(7, 'summary'), (16, 'summary')]
  [per-story x17] input=34 cache_write=0 cache_read=109774 output=21341 cost=$0.2993
  run 4: recall 5/6  false-drops 2/35  borderline 2/8  unmapped 0  malformed 0
          missed: [(3, 'headline')]
          FALSE DROPS: [(2, 'summary'), (7, 'summary')]
  best recall 5/6  worst false-drops 2
  OK (no egregious regression; review the scorecard for recall changes)
```
