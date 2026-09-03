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
| LLM cohesion gate, Sonnet 4.6, $0.05/run | **0.973 / 0.978** | 0.870 / 0.804 | **3 / 2 (5 total)** | 2.1 / 1.5 |
| V3 thin pass, deterministic, free | 0.755 / 0.838 | **0.983 / 0.938** | 56 / 38 (**94 total**) | ~4.5 |

(run 284 / run 285.)

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

## Why it fragments, and why that is the same finding as the stray post-check

V3's within-group TF-IDF is fitted on the group's own members. Inside a cluster every article
shares the tags that made it a cluster, so those tags carry near-zero IDF weight and what is
left to cluster on is the residual — largely noise. The conjunction gate then requires a
shared entity *and* event-cosine above a floor for any pair to link at all, and on a corpus
where the shared entity has been implicitly discounted, most pairs fail it.

This is the mirror image of `docs/2026-09-03-cohesion-stray-postcheck-probe.md`. There, tag
overlap could not tell an over-split stray from a correct one because high overlap is *how*
the cluster formed. Here, discounting that same overlap leaves nothing to hold a real story
together. Both directions say the same thing: `cluster_tags.json` does not encode event
identity, and no rule over it recovers what the extraction did not put in.

## Where this leaves Phase C

Three of the four cheap directions are now closed: fleet-wide join variants (PoC 3), a
deterministic stray post-check (the probe doc), and V3 scoped to the selected clusters (this
doc). The judge itself remains the only thing that has separated the reader-visible defects
without shredding the stories — 0.97 dominant retention, 5 over-split ids, six of six known
strays, $0.05 and 19 seconds a run — and its one unfixed failure is angle-splitting, which
needs a second question over its own partition and a run nobody has labelled.
