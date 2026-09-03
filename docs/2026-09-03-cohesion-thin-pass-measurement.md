# V3's conjunction gate as a thin pass over the selected clusters: rejected

*2026-09-03, ~22:40 UTC. Harness `scratch/poc-join-hubs/thin_pass.py` and
`scratch/poc-join-hubs/gate_same_metric.py` (gitignored). Pure Python over archived run
artifacts for runs 284 and 285; no model calls, no spend. Scored against the same 33 blind
labels as `docs/2026-09-03-cohesion-gate-measurement.md`.*

## What was tested and why

`scratch/poc-join-hubs/RESULTS.md` rejected all four join variants fleet-wide but named V3
(entity-AND-event conjunction) as the one worth carrying forward **scoped to the ~17 clusters
that actually ship** — the same scope the cohesion gate occupies — on the reasoning that
restricting it removes the fragmentation cost while keeping whatever signal it has. This is
that experiment.

The pass runs `variants.v3` inside each `cohesion.selected_groups()` group and keeps the
largest sub-cluster, matching the labels' own convention (`strays` = ids not in the largest
event). The pre-registered arm is `EVENT_COS_FLOOR = 0.10`, the value already in
`variants.py`; the floor sweep in the harness output is a sensitivity check and not a result,
because these are the labels every other 2026-09-03 measurement used.

## Verdict: rejected. It buys stray separation with fragmentation, at 19x the damage.

Both rows below are the **same metric, same dominant convention, same labels**:

| | dom. retained | strays separated | over-split ids | mean sub-clusters |
|---|---|---|---|---|
| LLM cohesion gate, Sonnet 4.6, $0.05/run | **0.973 / 0.976** | 0.870 / 0.792 | **3 / 2 (5 total)** | 2.1 / 1.5 |
| V3 thin pass, deterministic, free | 0.755 / 0.838 | **0.983 / 0.938** | 56 / 38 (**94 total**) | ~4.5 |

(run 284 / run 285. 16 labelled clusters per run, the same 32 for both rows.)

The gate's run-285 figures were 0.978 / 0.804 in this doc's first version, over 17 clusters
to the thin pass's 16. `gate_same_metric.py` was keying archived verdicts by the
`cluster_index` frozen in the artifact, while `thin_pass.py` called `selected_groups()` live
-- and label 285/234 resolves to cluster 235 today (`f616cf5`, `ee1f0fa`), so the gate was
scored on one extra cluster the thin pass never saw, at a free 1.0/1.0. The harness now
matches a verdict to a group by its article-id **set**. This is the same defect as
`docs/lessons/logic-errors/a-position-a-model-counts-is-not-an-identifier.md`, committed
this morning, reappearing in the measurement script written this evening; the correction
moves the gate by 0.012 and changes nothing.

The thin pass separates all five genuine known run-285 cases — helipad A70 and A666, the
OpenAI lawsuits A663, the NI reunification A492, the Xi-Trump AI remark A473 — where
fleet-wide V3 managed three. It also destroys the stories it is meant to protect: run 284's
53-article Iran cluster becomes 15 sub-clusters and retains 30% of its own dominant event;
run 285's 28-article escalation cluster becomes 13 and retains 24%.

**The high stray-separation number is the fragmentation artifact, not a result.** If a group
is split into 15 pieces and only the largest is kept, nearly every stray is "separated" by
arithmetic. This is the same failure the embedding gate was killed for on 2026-09-01 (a
metric maximised by random fragmentation), and it is why the two numbers must be read as a
pair: at parity on the known cases, the deterministic pass wrongly removes 94 ids where the
model call removes 5.

## "Largest" is not an unfair dominant rule

The obvious objection is that V3 fragments, so keeping the largest of 15 pieces punishes it
by construction. Three alternative rules, against a bar of dom_retained > 0.95, over-split
< 15, known cases >= 4/5:

| dominant rule | dom. retained | over-split ids | known cases |
|---|---|---|---|
| largest (used above) | 0.796 | 94 | 5/5 |
| most SELECT citations | 0.796 | 94 | 5/5 |
| greedy merge back to 0.5 of original size | 0.843 | 61 | 4/5 |
| best match to the cluster's story label | 0.752 | 111 | 5/5 |

None clears the bar; the citation rule is identical to largest because SELECT cites the whole
group. The rule is not what is failing.

## Why it fragments: two independent effects, not one

Isolating gate on/off against group-scoped and fleet-scoped IDF, at threshold 0.80, on the
three worst clusters (`dom_retained`):

| | gate on | gate off |
|---|---|---|
| **fleet IDF** | 0.444 / 0.667 / 0.700 | 1.000 / 1.000 / 1.000 |
| **group IDF** | 0.222 / 0.238 / 0.300 | 0.278 / 0.619 / 0.550 |

(run 285 ci3 / run 285 ci6 / run 284 ci3.)

Each half damages on its own. **The conjunction gate alone**, with fleet-wide IDF and no
group scoping, still drops retention to 0.44-0.70: `variants.py`'s entity test is a raw
lowercased string intersection (`ent_sets[i] & ent_sets[j]`), so two articles about one event
fail it whenever the extractor wrote "Putin" for one and "Vladimir Putin" for the other, and
the `primary_event` cosine floor rejects more. **Group-scoped IDF alone**, with the gate off,
drops it to 0.28-0.62: fitted on the group's own members, the tags that made the cluster
carry near-zero weight and what is left to link on is the residual. The only cell that
returns 1.000 is gate-off with fleet IDF, which is V0's similarity function reproduced
verbatim — not a repair of V3.

An earlier version of this section attributed the whole failure to the second effect and said
the gate fails "on a corpus where the shared entity has been implicitly discounted". That is
wrong about the entity half: it never passes through TF-IDF at all. Two compounding causes,
and the rejection is more robust for it, not less.

This is still the mirror image of `docs/2026-09-03-cohesion-stray-postcheck-probe.md`. There, tag
overlap could not tell an over-split stray from a correct one because high overlap is *how*
the cluster formed. Here, matching on raw entity strings and discounting that same
overlap each leave too little to hold a real story together. Both directions say the same
thing: `cluster_tags.json` does not encode event
identity, and no rule over it recovers what the extraction did not put in.

## Where this leaves Phase C

Three of the four cheap directions are now closed: fleet-wide join variants (PoC 3), a
deterministic stray post-check (the probe doc), and V3 scoped to the selected clusters (this
doc). The judge itself remains the only thing that has separated the reader-visible defects
without shredding the stories — 0.97 dominant retention, 5 over-split ids, six of six known
strays, $0.05 and 19 seconds a run — and its one unfixed failure is angle-splitting, which
needs a second question over its own partition and a run nobody has labelled.
