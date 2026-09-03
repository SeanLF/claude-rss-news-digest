# Cohesion gate replay, runs 284 and 285, against blind labels

*2026-09-03. Task 4 of `docs/2026-09-03-cohesion-gate-plan.md`. Harness `bin/eval-cohesion
--runs 284,285 --reps 3` (d6b6996) running the real gate (`cohesion.py`, 9012b40..6a5db65) with
`claude-sonnet-4-6`, thinking disabled, 12 clusters per call. Labels:
`docs/2026-09-03-cohesion-gate-labels.json`, written from titles and snippets before any judge
ran, by one reader. 33 selected clusters labelled (one singleton is not judged).*

## Verdict

**Gate failed as stated: 67% event-count agreement against a floor of 80%, and 5 over-splits
against a floor of 0.** Every one of the six known run-285 strays was separated (the helipad
and the WSJ opinion, the OpenAI lawsuits, the Treasury-yields piece in the SCO cluster, the
Northern Ireland remark, the Trump AI-race remark), the stray set matched the label exactly on
20 of 33 clusters, and the whole thing costs $0.05 and 19 seconds per run. The gate is not
deployed. What failed is specific and is written below.

## Numbers

| | modal verdict over 3 reps |
|---|---|
| clusters scored | 33 |
| event-count agreement | 22 (67%) |
| stray-set Jaccard | mean 0.80, median 1.00; exact on 20 |
| over-splits (labelled same-event ids removed) | 5, on 3 clusters |
| known run-285 cases separated | 6 of 6 |
| cost per gate run | $0.048 API-equivalent (first call of a container $0.10) |
| seconds per gate run | 19 |
| verdicts stable across 3 reps | 30 of 33 clusters identical; cluster 6/285 differed once, cluster 3/284 and 48/284 differed on one id |

Per-rep artifacts are under `data/eval-cohesion/<run>/cluster_cohesion.<rep>.json` (gitignored).

## What disagreed, and why the count metric is the wrong one

**Count disagreements (11) are mostly the judge grouping strays, not missing them.** On the
Joshua Wong cluster (284/2) the judge returned 2 events where the label said 10: it put the nine
unrelated Hong Kong items in one "other" group. The stray set was identical (Jaccard 1.0). Same
on 284/23 (3 vs 5, Jaccard 1.0). Event count penalises a judge for not enumerating junk it
correctly removed; the stray set is the measure that matters to WRITE, and the plan should have
said so.

**Under-splits (the judge kept what the label called strays)** on 284/3, 284/48, 284/102,
285/7, 285/48, 285/2. These are the judge being more conservative than the reader: on the big
Iran cluster it left sanctions, fuel-crisis and Hormuz-bypass pieces in the story and removed only
the Aug 31 tanker attacks and one economy piece. Under-splitting is today's behaviour; it costs
nothing new.

**Over-splits (the judge removed what the label called the same event)**, 5 ids on 3 clusters,
and these are the finding:

| cluster | what the judge did | consequence |
|---|---|---|
| 284/109 "Russia signals support for Iran" | split Putin's Ukraine remarks (4 articles) from his Iran support (3) at the same appearance; the size rule made Ukraine the dominant | WRITE would be handed the Ukraine articles for a story SELECT chose about Iran |
| 285/140 "METR report on the OpenAI incident" | split the METR report (1) from OpenAI's letter responding to it (2); the letter became dominant | the story loses its own subject |
| 285/6 the Iran escalation | split the Red Crescent's ICC request over the wedding strike (1 article) from the strike, in 2 of 3 reps | one cited article gone; mild |

Two mechanisms, and neither is fixed by tuning on these 33 clusters:

1. **The dominant group is chosen by size, and size does not know which facet SELECT
   chose.** I re-scored the saved verdicts with "the group holding most of SELECT's citations"
   instead of "the largest": nothing changed, because SELECT cites the whole cluster (all 9
   ids on 284/109). The information that names the facet is the cluster's `story` label, which
   the judge is never shown. Next iteration: show the judge the label and ask which event it
   names; the dominant is that event, not the biggest.
2. **The rubric lacks two cases the judge splits on:** a report and the response it prompted,
   and one speaker's remarks at one appearance. Both are "follow-ups of one event" under the
   rubric's own words and both were split anyway. Next iteration: two examples in the rubric.

Both changes need a **fresh run and fresh blind labels** before they are scored. Re-scoring
these 33 after changing the prompt to fit them would make the gate a fit, not a measurement.

## What passed, and what it is worth

The reader-visible defects that started this (run 285's helipad brief, the lawsuits bolted onto
the OpenAI story, the yields piece in the SCO cluster) were all separated, in all three reps,
for five cents. Twenty of 33 clusters came back exactly as the labels had them, and the judge
never invented an id or broke the partition. As a splitter of junk it works. As a chooser of
which facet is the story it does not yet, and that is the part a WRITE branch depends on.

## Raw output

```
COHESION gate replay  runs=[284, 285]  reps=3  model=claude-sonnet-4-6  labels=33
[run 284 rep 1] outcome=completed judged=16 split=10 strays_removed=34 cost=$0.103 23s
[run 284 rep 2] outcome=completed judged=16 split=10 strays_removed=30 cost=$0.032 17s
[run 284 rep 3] outcome=completed judged=16 split=10 strays_removed=34 cost=$0.032 21s
[run 285 rep 1] outcome=completed judged=17 split=5 strays_removed=8 cost=$0.074 17s
[run 285 rep 2] outcome=completed judged=17 split=6 strays_removed=9 cost=$0.024 15s
[run 285 rep 3] outcome=completed judged=17 split=6 strays_removed=9 cost=$0.024 22s
=== MODAL VERDICT vs LABELS ===
  [diff] run 284 cluster   3: judge 3 vs label 7, jaccard 0.385  missed ['A147', 'A154', 'A16', 'A315', 'A450', 'A451', 'A524', 'A657']
  [ok ] run 284 cluster  18: judge 1 vs label 1, jaccard 1.0
  [diff] run 284 cluster   2: judge 2 vs label 10, jaccard 1.0
  [diff] run 284 cluster  23: judge 3 vs label 5, jaccard 1.0
  [diff] run 284 cluster 102: judge 2 vs label 4, jaccard 0.333  missed ['A596', 'A603']
  [ok ] run 284 cluster   1: judge 1 vs label 1, jaccard 1.0
  [ok ] run 284 cluster   6: judge 1 vs label 1, jaccard 1.0
  [diff] run 284 cluster  48: judge 2 vs label 6, jaccard 0.2  missed ['A216', 'A663', 'A674', 'A676']
  [ok ] run 284 cluster  50: judge 2 vs label 2, jaccard 1.0
  [ok ] run 284 cluster  77: judge 2 vs label 2, jaccard 1.0
  [ok ] run 284 cluster  99: judge 1 vs label 1, jaccard 1.0
  [ok ] run 284 cluster 107: judge 1 vs label 1, jaccard 1.0
  [diff] run 284 cluster 109: judge 4 vs label 3, jaccard 0.4  over-split ['A324', 'A350', 'A660']
  [ok ] run 284 cluster 129: judge 2 vs label 2, jaccard 1.0
  [ok ] run 284 cluster 152: judge 5 vs label 5, jaccard 1.0
  [ok ] run 284 cluster 219: judge 1 vs label 1, jaccard 1.0
  [diff] run 285 cluster   6: judge 2 vs label 8, jaccard 0.0  over-split ['A15']  missed ['A17', 'A535', 'A572', 'A584', 'A654', 'A655', 'A7']
  [diff] run 285 cluster   7: judge 3 vs label 4, jaccard 0.667  missed ['A549']
  [ok ] run 285 cluster  47: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster   3: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster  46: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster   5: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster   4: judge 2 vs label 2, jaccard 1.0
  [diff] run 285 cluster  48: judge 1 vs label 2, jaccard 0.0  missed ['A94']
  [ok ] run 285 cluster  21: judge 1 vs label 1, jaccard 1.0
  [diff] run 285 cluster   2: judge 1 vs label 2, jaccard 0.0  missed ['A161']
  [ok ] run 285 cluster  33: judge 3 vs label 3, jaccard 1.0
  [ok ] run 285 cluster  34: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster  75: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster 234: judge 2 vs label 2, jaccard 1.0
  [ok ] run 285 cluster  22: judge 1 vs label 1, jaccard 1.0
  [ok ] run 285 cluster 109: judge 1 vs label 1, jaccard 1.0
  [diff] run 285 cluster 140: judge 3 vs label 2, jaccard 0.5  over-split ['A232']
event-count agreement: 22/33 (67%)
stray-set jaccard: mean 0.80  median 1.00
over-splits on the modal verdict: 5
known cases separated: 285/7/A473=yes, 285/4/A492=yes, 285/33/A70=yes, 285/33/A666=yes, 285/234/A431=yes, 285/140/A663=yes
cost: $0.289 over 6 gate runs ($0.048/run), 19s/run
GATE (>=80% count agreement, 0 over-splits, all known cases separated): FAIL
```
