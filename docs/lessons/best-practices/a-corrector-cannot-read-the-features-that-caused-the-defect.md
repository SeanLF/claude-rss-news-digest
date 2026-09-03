---
title: A corrector that reads the same features as the generator has no information the generator did not already act on
date: 2026-09-03
category: best-practices
module: cohesion, cluster_extractjoin
problem_type: best_practice
severity: medium
applies_when:
  - Adding a deterministic post-check, filter, or rescue rule on top of a component's output
  - The proposed features are the ones the component itself was built from
  - A cheap rule is offered as a substitute for a model call, on data the model call already saw
tags: [clustering, cohesion, feature-selection, post-check, negative-result, evaluation]
---

## The lesson

Before building a corrector, ask what it can see that the thing it corrects could not. If the
answer is "the same features", it can only reproduce the original decision. High overlap on
the features a system fused by is not evidence the fusion was right — it is a restatement of
why the fusion happened.

## Where it bit

The deterministic join in `cluster_extractjoin.py` groups articles by weighted overlap of
three per-article tag bags: `{entities: 3, keywords: 1, primary_event: 2}` in
`cluster_tags.json`. The cohesion judge (`cohesion.py`) exists to split the clusters that
fusion over-merged, and on 2026-09-03 it failed its gate on 5 over-splits — it removed
articles that belonged to the story.

The proposed fix was a free post-check on the judge's strays: keep a stray that shares the
story's core entities with the dominant group. Replayed against the blind labels
(`docs/2026-09-03-cohesion-stray-postcheck-probe.md`), the feature ran the wrong way. Strays
that should have been kept had *lower* mean entity overlap (0.530) than strays that were
correctly removed (0.620). At the pair level the two classes were indistinguishable — mean
entity Jaccard 0.277 for pairs that should merge, 0.281 for pairs that must stay split — and
the single highest score in the set, a perfect 1.0, was a pair that must stay split.

The mechanism is not subtle in hindsight. Every junk drawer the gate exists to break up is,
by construction, a set of articles with high tag overlap: six unrelated Hong Kong stories
each carrying "Hong Kong" scored 1.0, as did the White House helipad bolted onto a White
House story. The rubric had said so in words all along — "a typhoon and a company's earnings
that both happen in Hong Kong = 2" — and the rule would have re-merged exactly that.

The most natural repair fails hardest. Dropping the entity that every member of the cluster
shares -- the "Hong Kong" that made the junk drawer -- is the obvious way to make the feature
discriminative. It pushes the two classes further apart in the *wrong* direction: over-split
mean 0.000 against correct-stray 0.092. IDF-weighting the entities does the same, more mildly.
There is no residual signal to recover, because the tags never carried one.

## What to do instead

Give the corrector a source the generator never read. The judge works because it reads titles
and snippets; the join only ever saw the tags extracted from them. If a corrector on the
judge's output is wanted, it has to read the articles too — which makes it another model
call, not a rule. A cheap rule that reads the generator's own inputs is not a cheaper version
of the model call; it is the generator again.

## Cost of finding out

Two replay scripts over committed artifacts, no model calls, about twenty minutes. The
alternative was shipping a rule that re-merged 26 of 38 correctly removed strays.

## See also

- `docs/2026-09-03-cohesion-stray-postcheck-probe.md` — the numbers.
- `docs/2026-09-03-cohesion-gate-measurement.md` — the gate failure this was meant to fix.
- `a-lexical-detector-is-anti-correlated-with-a-rewording-defect.md` — same shape: a detector
  whose features are the ones the defect moves.
- `an-unfiltered-sibling-tells-generator-from-validator.md` — the general question of what a
  validator can see that the generator could not.
