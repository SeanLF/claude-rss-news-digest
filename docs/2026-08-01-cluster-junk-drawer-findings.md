# CLUSTER junk drawers on current production runs (2026-08-01)

Sean noticed shipped cards bundling unrelated Asian stories ("Japan, Korea or China lately
have a story with other Asian things without ties"). This measures how common that is on
runs the reader actually received, and revisits the June adjudication's verdict in light of
what has changed since.

Harness: `scratch/kitchen-sink/` (`scan.py`, `trace.py`, `members.py`, `purity.py`,
`feedvol.py`, `adjudicate_clusters.py`). Raw verdicts: `scratch/kitchen-sink/out/`.

## TL;DR

**~23% of shipped clusters contain three or more unrelated stories** (21/91 over runs
241-247, 9.4/run). But the reader-visible damage is ~5x smaller (~5 clearly-bad cards in
111) because **WRITE usually writes the dominant story and silently drops the strays**. So
the durable argument for fixing this is **corrupted selection weight**, not ugly prose: a
17-article "story" that is really 10 stories competes for a must-know slot on false size.

The June conclusion that the entity-conjunction fix is "closed" rests on a run configured
differently from production (see §4) and is weaker than it reads.

## 1. What was measured

For every cluster of >=4 articles in runs 241-247, a judge saw ONLY the member headlines --
no cluster label, no summary, nothing hinting at an answer -- and reported how many distinct
news stories were present. Judged twice; a cluster counts as a junk drawer only if BOTH
passes said >=3 stories.

| | |
|---|---|
| all clusters >=4 articles | 66/220 (30%) |
| **clusters that shipped** | **21/91 (23%)** |
| per run | 9.4 |
| pass agreement (exact `n_stories`) | 186/220 (85%) |

Worst shipped: `Hong Kong teen convicted of sedition` (12 articles, **11 stories**),
`Typhoon Noul impact on Hong Kong` (17 articles, **10 stories**), `US-Russia diplomats
Manila talks` (17 articles, 7 stories).

**Caveats, all of which matter:**
- **Not comparable to June's "1-2 junk drawers".** That used `analyze.py`'s definition (>=4
  articles spanning >=3 *gold* stories, requiring a reference clustering). This is gold-free
  LLM adjudication on headlines. The jump is suggestive, not proven; the absolute rate is the
  defensible number.
- **66 is a floor.** Unanimity across both passes is required, so the 15% of clusters where
  the passes disagreed are excluded.
- **"Shipped" is loose** -- it means >=1 article from the cluster was cited in a published
  card, not that the whole grab-bag became one card.
- The judge is Sonnet, which also produced the extraction tags. The join itself is
  deterministic, so self-preference risk is mild but non-zero.

## 2. Mechanism (traced, not inferred)

Both hand-found kitchen sinks trace to a SINGLE cluster whose label names only one member
story. `Typhoon Noul makes landfall in southern China and Hong Kong` holds 23 articles: ~5
typhoon, 8 Shein IPO, and ~10 unrelated SCMP Hong Kong items (student housing, a minibus
crash, a plagiarising judge, Alzheimer's care).

`_tag_bag` (`cluster_extractjoin.py:256`) weights `entities * 3` against `primary_event * 2`
-- the join ranks WHERE above WHAT. Articles sharing a place name merge despite disjoint
events. TF-IDF amplifies rather than fights this: a place name that is rare corpus-wide but
common inside one feed has high IDF, making it close to an ideal false attractor.

**It is not feed volume.** Reuters files ~96 articles/run and does not junk-drawer;
`scmp_asia` is not even top-8 by volume and dominates the junk drawers. The variable is
**entity concentration** -- many articles sharing one place -- not article count.

Three hypotheses died on checking, recorded so they are not re-run:
- *Feed mix changed* -- no. `scmp_asia`/`upi`/`nikkei_asia`/`rappler`/`daily_maverick` were
  all added 2026-02-04, well before the June measurement.
- *Corpus outgrew the threshold* -- weak. Current runs (~530-700 to cluster) sit near run
  205's 465, which was in the tuning bracket. The code does warn the granularity-matching
  threshold rises with corpus size, so this is not zero, but it is not a tripling either.
- *Raw feed volume* -- no, see above.

## 3. There is no split stage

Post-join, the only operation is `_merge_same_story`, which merges *more* (folds strays into
anchors). The extract-join design doc adopted the stage explicitly because the previous
holistic stage **over-split**. So every force in the pipeline pushes toward merging and
nothing pushes back -- a merge ratchet, of which junk drawers are the predictable end state.

## 4. Why "the conjunction lever is closed" deserves a re-test

`docs/2026-06-26-cluster-eval-methodology.md` closed the entity+event conjunction fix: it
raised pair-precision to 0.967 but lost recall (0.887 -> 0.844), landing 0.008 below band.

Two reasons that verdict may not transfer to production:
1. The adjudicated row is labelled **`extract-join (Haiku+tfidf)`**, and the stated cause of
   the recall loss is that *"the entity gate also blocks valid merges where **Haiku's**
   entity strings drifted across extraction batches."* Production has shipped **Sonnet**
   extraction since 2026-07-02 (`CLUSTER_EXTRACT_MODEL`), chosen precisely because Haiku cost
   a ~10% source-diversity dip. The failure mode is an artifact of a replaced extractor.
2. The doc's *residual* errors were semantically-close sub-events (G7 AI-chips vs US-China AI
   race) that no gate can separate. The junk drawers measured here are the **other** class --
   shared place, wholly different events -- which is exactly what drove precision to 0.967.

This is not a claim the lever works on Sonnet tags. It is a claim it was never tested there.

## 5. Recommendation

**A thin cohesion gate on the ~17 SELECTED clusters, pre-WRITE.** This is the architecture
the June doc itself named as the open path ("cheap-clustering-then-thin-Sonnet-refine-on-
borderline"), and it was never built.

Why there rather than in the join:
- It touches only what ships (~17 clusters), not all ~200, so blast radius is tiny and
  additive -- no verdict means no change.
- A "split" verdict can hand WRITE two stories instead of one, fixing the card AND the
  selection weight.
- It sidesteps the ARI/BCubed/band measurement swamp entirely: since the global partition is
  unchanged, the metric is task-grounded by construction (junk-drawer cards before vs after),
  which is the project's own standing rule.

Cost: a cohesion judgement needs only the card/cluster text, not the source articles, so it
is ~$0.03-0.05/run against a ~$2.50-3.00 run -- roughly 1-2%.

Explicitly NOT recommended: reweighting `_tag_bag` or re-opening conjunction as a shipping
change. Both perturb every cluster boundary in the run for a win the gate obtains on 17.

## Reproduce

```bash
# junk-drawer adjudication (parallel; ~13 min at 8-wide, ~99 min sequential)
mkdir -p scratch/kitchen-sink/out
docker compose run --rm -v "$PWD/scratch:/app/scratch" -v "$PWD/data:/app/data" \
  --entrypoint /app/.venv/bin/python3 digest-newsroom \
  /app/scratch/kitchen-sink/adjudicate_clusters.py --runs 241-247 --passes 2 --concurrency 8

# progress while it runs (stdout buffers when detached; this file does not)
cat scratch/kitchen-sink/out/progress.txt

# hand-inspection helpers
python3 scratch/kitchen-sink/scan.py                      # pivot-word screen over shipped cards
python3 scratch/kitchen-sink/members.py 246 "Typhoon Noul" # what a cluster actually contains
```
