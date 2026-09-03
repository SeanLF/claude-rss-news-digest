# A tag-overlap post-check on the cohesion judge's strays: dead before it was built

*2026-09-03, ~22:20 UTC. Probes: `scratch/probe_stray_entities.py` and
`scratch/probe_merge_pairs.py` (gitignored; both are pure replays of committed artifacts and
cost nothing). Inputs: the modal verdicts over 3 reps in `data/eval-cohesion/{284,285}/
cluster_cohesion.{1,2,3}.json`, the archived `cluster_tags.json` for runs 284 and 285, and the
blind labels in `docs/2026-09-03-cohesion-gate-labels.json`. No model calls.*

## The proposal

After `docs/2026-09-03-cohesion-gate-measurement.md` failed its gate on 5 over-splits, the
next step written into the handoff was a deterministic post-check on the judge's output:
**a stray that shares the story's core entities with the dominant group stays.** The features
are already in `cluster_tags.json` (per-article `entities`, `keywords`, `primary_event`), so
this is free at run time.

## Verdict: do not build it. The feature is not merely weak, it is the wrong feature.

The over-split strays the rule is meant to rescue have *lower* entity overlap with the
dominant than the correct strays it must not touch. Every threshold destroys more than it
saves.

### Probe 1 — per stray (5 over-splits, 38 correct strays)

| feature | over-split mean | correct-stray mean |
|---|---|---|
| entity fraction | 0.530 | **0.620** |
| keyword fraction | 0.040 | 0.093 |
| `primary_event` token Jaccard | 0.066 | 0.079 |

Best threshold on entity fraction: `t=0.5` rescues 3 of 5 over-splits and re-merges 26 of 38
correct strays. The six Hong Kong junk-drawer articles — a pension proposal, a rape
conviction, a school-bus crash, a tram failure, class cuts, a dog-breed case — all score
**1.0**, the maximum, because every one of them carries the entity "Hong Kong". So does the
run-285 helipad brief, at 1.0.

### Probe 2 — per pair of the judge's events, which is the real decision unit

Asking "should these two of the judge's events be merged back?" rather than "should this one
stray stay" makes the classes indistinguishable:

| feature | should-merge (n=3) | should-stay-split (n=26) |
|---|---|---|
| entity Jaccard | min 0.080 med 0.250 max 0.500 mean **0.277** | min 0.059 med 0.250 max 1.000 mean **0.281** |
| keyword Jaccard | mean 0.004 | mean 0.012 |
| `primary_event` Jaccard | mean 0.052 | mean 0.048 |

The single highest entity Jaccard in the set, 1.0, is a pair that must stay split.

## Why, and this is the part that generalises

`cluster_tags.json` is the **join's own input**. `tag_bag_weights` is `{entities: 3,
keywords: 1, primary_event: 2}`: the deterministic join fused these clusters by weighted
overlap of exactly these three bags. High tag overlap is therefore not evidence that two
groups are one event — it is the definition of how they came to be one cluster in the first
place. Every junk drawer the gate exists to break up is, by construction, a set of articles
with high tag overlap. A post-check reading those tags has no information the join did not
already act on. Lesson filed as
`docs/lessons/best-practices/a-corrector-cannot-read-the-features-that-caused-the-defect.md`.

## What the probes did establish about the remaining defect

Checking the over-split cases against their labels changed the picture of what the
label-aware gate (f616cf5) fixed:

- **284/109** — the label calls Putin's Kyrgyzstan appearance one event (Ukraine remarks
  A223/A412/A457/A562 + Iran remarks A324/A350/A660). The judge splits the appearance in two
  in every rep. The pre-label gate took the larger half (Ukraine) and over-split 3 ids;
  PoC 2 reports the label makes the judge take the Iran half, which over-splits the other
  **4**. The fix relocates the over-split; it does not remove it.
- **285/140** — label: METR report + OpenAI's reply = one event. The judge returns them as
  two. Pre-label dominant was the reply (over-split 1); with the label it becomes the report
  (over-split 2).
- **285/6** — the Red Crescent ICC request split off the wedding strike, in 2 of 3 reps.

So angle-splitting is the whole of the remaining defect, it survives the label, and on these
two clusters the label-aware gate makes the id count slightly *worse* while making the facet
choice right. Neither number is a measurement of the shipped gate: these artifacts predate
f616cf5 and the label-aware counts are read from PoC 2's write-up, not re-derived.

## Where this leaves the gate

`COHESION_ENABLED` stays off. Three directions remain, none of them a tag post-check:

1. **Ask the judge to merge, not just to split.** The judge already produces the partition;
   the missing step is a second question over its own events ("do any two of these describe
   one event?"), which reads titles and snippets — information the join never had. This is
   the only candidate with an independent signal, and it is a model call, not a rule.
2. **Accept angle-splitting and change what the dominant means:** hand WRITE the dominant
   *plus* any event the judge lists adjacent to it, i.e. use the gate only to drop groups
   that are clearly separate. This trades the 5 over-splits for a weaker filter.
3. **Ship nothing and spend the effort on Phase B**, where the measurement is cheaper and
   the gate is the existing eval floor.

None should be built before a run nobody has labelled is labelled blind, per the standing
rule not to tune on these 33.
