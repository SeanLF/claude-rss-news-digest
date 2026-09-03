# Three clustering PoCs, 2026-09-03

*Run in parallel by three Sonnet subagents in gitignored `scratch/poc-*/` directories, after the
cohesion gate failed its replay gate (`docs/2026-09-03-cohesion-gate-measurement.md`). Each
agent's numbers were re-derived from its saved outputs before being written here; the
per-rep artifacts are under `scratch/` and are not committed. All three are negative or
mixed. One of them found a real bug, fixed the same day.*

## The finding that mattered: SELECT's `cluster_index` drifts, and the fan-out trusted it

The join PoC noticed that one "known stray" (the Treasury-yields article in run 285's SCO
brief) was never in the story's cluster. SELECT had written `cluster_index: 234`, a lone
yields article, for a story whose five citations all live in cluster 235. The fan-out built
the branch as `cluster[234] ∪ citations`, and WRITE saw the yields piece.

Across the archive: **182 of 1291 selected stories (14%, 79 runs) carry a `cluster_index`
that holds none or few of their own citations**, mostly off by one to three. `threads.py`
stopped trusting the index in July (run 247, 7 of 12 should_know entries), and
`utils.cluster_for_articles` exists for that reason; the per-story fan-out of 2026-09-01
did not use it. The SELECT PoC saw the same drift in fresh replays (index swaps between
adjacent clusters in 7 of 50 story instances).

**Fixed (99f6ed5, ee1f0fa):** the citations decide the cluster in both the fan-out and the
cohesion gate; SELECT's index is kept in the run artifact as `selected_cluster_index`. It is
a context-hygiene bug, not the junk-drawer cause: bolt-on summaries are no more common on
drifted stories (13.5%) than on correctly indexed ones (20%).

## PoC 1: SELECT cites only the event (idea 25) -- no effect

Shipped `select.md` against a copy with one added line ("`article_ids` lists only the
articles about the event this story is; leave out cluster members about a different event"),
three reps each on run 285's inputs, scored against the blind labels.

| | arm A (shipped) | arm B (one line added) |
|---|---|---|
| labelled strays SELECT still cited | 15 of 15 story instances at 100% | 15 of 15 at 100% |
| labelled dominant ids dropped | 0 of 43 | 0 of 43 |
| cost per rep | $0.30-0.38 | same |

The added line sits one paragraph below the existing rule "include ALL relevant article_ids
from the cluster" and lost to it every time. Not a finding about whether SELECT *can* cite
the event; a finding that a conflicting instruction does nothing, which the project already
knew. Rewriting the existing rule rather than adding one was not tested.

**Side finding:** SELECT's choices are unstable rep to rep. Within one arm, the clusters
selected across three reps overlap at Jaccard 0.24-0.34; only the must_know core repeats.
Any measurement that "replays SELECT" is measuring a different digest each time.

## PoC 2: cohesion gate shown the cluster label, plus two rubric cases (ideas 26 and 6) -- mixed

Arm B monkeypatched the judge: the cluster's `story` label in each GROUP header, "the first
event you list is that story's event", and two rubric examples (a report and the response it
prompted; one speaker's remarks at one appearance). The agent labelled run 283 blind before
any judge ran (12 clusters, 7 multi-event), then ran both arms three times on 283 (fresh)
and on 284/285 (the labels the change was designed against, reported separately).

| run 283, fresh labels | arm A (shipped) | arm B (4 clean reps) |
|---|---|---|
| event-count agreement | 8/12 (67%) | 7/12 (58%) |
| stray-set Jaccard, mean | 0.76 | 0.76 |
| over-splits on the modal verdict | 8 (clusters 44, 121) | 2 (clusters 3, 109) |

On the tuned-on runs arm B raised over-splits from 5 to 9. On the two cases the label was
meant to fix (Putin's Ukraine/Iran remarks; METR report vs OpenAI's reply) **the label made
the judge pick the right facet as dominant in both**, but the judge still split the pair, so
the story keeps the right half and loses the other. On the fresh run arm B fixed the
ten-story Hong Kong junk drawer (Jaccard 0.17 to 1.0) and introduced one over-split on a
clean three-article cluster. Neither arm could untangle run 283's Iran cluster (a five-article
diplomacy thread bundled with sanctions and escalation strands; Jaccard 0.0 and 0.1). Two of
the first three arm-B reps returned unparseable replies; four re-runs did not, so the rate is
unknown. Arm A's own rep-to-rep noise: 5 of 45 clusters changed stray set between reps.

Reading: showing the label fixes *which* facet wins, which was the deployable-blocking
defect; it does not stop the judge splitting angles off one event, and the rubric examples
did not either. Twelve fresh clusters is too few to call 2 vs 8 over-splits a result.

## PoC 3: structural changes to the join (ideas 2, 14, 16, 20) -- none shippable fleet-wide

No model calls. The archived Sonnet tags for runs 284/285 re-joined under four variants,
scored on the labelled selected clusters. V0 reproduced both archived partitions exactly.

| run | variant | dominant retained | strays separated | clusters | singletons |
|---|---|---|---|---|---|
| 284 | V0 production | 1.000 | 0.375 | 303 | 196 |
| 284 | V1 no place entities | 0.926 | 0.754 | 349 | 233 |
| 284 | V2 mutual-kNN | 0.732 | 0.761 | 384 | 243 |
| 284 | V3 entity AND event | 0.887 | 0.529 | 362 | 258 |
| 284 | V4 size cap, re-split | 0.874 | 0.495 | 356 | 233 |
| 285 | V0 production | 1.000 | 0.588 | 318 | 203 |
| 285 | V1 | 0.866 | 0.706 | 379 | 261 |
| 285 | V2 | 0.788 | 0.706 | 390 | 254 |
| 285 | V3 | 0.880 | 0.807 | 378 | 267 |
| 285 | V4 | 0.859 | 0.706 | 375 | 254 |

Every variant buys stray separation by scattering the dominant event and roughly doubling
singletons, the whole-corpus blast radius the August recommendation was written to avoid.
V3 (a pair joins only if it shares an entity AND its event phrases are similar) is the best
of the four and the only one that separates the helipad at the join level, but its floor is
an untuned placeholder. The one thing the agent recommends and did not run: V3 as a thin pass
over only the selected clusters, the scope the cohesion gate already occupies.

## Where this leaves clustering

- The junk-drawer mechanism (place names as hubs) is confirmed by V1's stray separation and
  by the same variant's scatter; the join cannot be fixed by one lever fleet-wide.
- The cohesion gate separates junk reliably and cheaply and chooses the facet correctly when
  shown the label. Its remaining failure is splitting angles off one event. That is the
  thing to measure next, on fresh labels, with the label change kept and the rubric examples
  replaced by something structural (a floor on group size relative to the cluster, or "an
  article that names the story's actor and action stays with the story").
- SELECT's instability is a measurement problem for everything downstream of it and deserves
  its own number before any SELECT prompt change is scored.
