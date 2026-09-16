# SELECT order dependence: real but small, about one pick in 16; sampling noise is the larger term (2026-09-16)

Follow-up to `docs/2026-09-16-sota-and-competitor-recheck.md` §1.1, which asked one question of
[arXiv 2608.26762](https://arxiv.org/abs/2608.26762) (order alone moves an LLM scorer's retained
set by 16 to 34%): does that transfer to our SELECT stage, or was the rep-to-rep instability
recorded on 2026-09-03 (Jaccard 0.24 to 0.34) all sampling noise?

This doc was rewritten three times in one day under adversarial review, and the earlier
versions each drew the wrong conclusion from the same data. The history is kept below because
the mistakes are the reusable part.

## TL;DR

**Order moves about one of SELECT's 16 picks on run 298 (0.6 to 1.3 depending on which
arm's self-agreement you count from, 4 to 8% of the set), well under the paper's 16 to 34%,
and it does not change how self-consistent SELECT is.** Five reps of the
shipped `select.md` per arm:

| arm | cluster order the model read | within-arm Jaccard, all picks | must_know |
|---|---|---|---|
| fixed | as archived | 0.51 | 0.51 |
| shuffled | a fresh uniform permutation per rep | 0.47 | 0.46 |
| sorted | size-descending | 0.55 | 0.75 |

Two exact permutation tests, both now in the harness:

- **Gap** (does an arm add variance?): no pair differs (two-sided p 0.33 to 0.63), and five
  reps could have seen a paper-sized effect (the fixed-vs-shuffled gap needs 0.07 to reach
  p ≤ 0.05; a 16% set movement would show as about 0.2).
- **Shift** (does an arm move the picks to different clusters, keeping self-consistency?):
  fixed vs shuffled p = 0.008, the single most extreme of the 126 distinct splits. Within
  agreement 0.51 (fixed) or 0.47 (shuffled) versus cross agreement 0.43 is 11.2 or 10.5
  shared picks against 9.9, so 0.6 to 1.3 picks per digest depend on the order alone; the
  statistic itself uses the mean of the two, about 1.0. Sorted vs shuffled shows the same (p = 0.048);
  sorted vs fixed does not (p = 0.10): the two informative orders agree with each other.

Sampling noise is still the larger term: reruns in the same order already disagree on half
their picks. Order-averaging over k permutations would remove the one-pick component for about
$1.25 a run at k = 3 (the fifteen recorded calls averaged $0.42); whether that is worth buying is an editorial and cost call, recorded as
open, not a recommendation. The size-sorted order costs nothing and on this run matched the
archived order everywhere and beat it on must_know agreement (0.75 vs 0.46 shuffled, one of six
gap comparisons at p = 0.016, Bonferroni 0.095). Both need a second day before anything ships.

## What the three write-ups got wrong, in order

1. **First pass: "order is a real component" (fixed 0.51 vs shuffled 0.385, one-sided
   p = 0.028).** Defects: the five fixed reps all ran before the five shuffled reps (file
   mtimes 10:52 to 10:54 vs 10:55 to 11:00), so arm was confounded with wall-clock time; the
   harness wrote `clusters.json` compactly where production writes `indent=2`; and the
   archived order is not neutral (clusters are emitted by first article id, so all eleven
   size-10+ clusters sit in the first 105 of 289) while inside the shuffled arm there was no
   position preference to see (five-plus-article clusters picked at mean read position 145,
   unpicked 147, uniform 144; second pass 142 and 149). Outputs kept as
   `docs/proposed/2026-09-16-select-order/run298/*-v1-superseded.*`; the mtimes are not in
   the tree and rest on the session record.
2. **Second pass: "not demonstrated; sampling noise is the whole story."** Arms interleaved,
   file in production shape, third arm added; the gap shrank to 0.047 and no pair was
   significant. The error was the statistic: the within-arm gap only sees an arm that adds
   variance. An arm that moves the picks to different clusters while staying just as
   self-consistent is invisible to it. The cross-arm means were computed and printed but never
   tested.
3. **This version.** The shift test (mean within-arm agreement minus cross-arm agreement,
   one-sided over the same 252 splits) is added to the harness with unit tests, including the
   case that fooled version 2 (two perfectly self-consistent arms on disjoint clusters: gap
   0, shift 1). Its negative control on this data: over all 252 relabellings of the pooled
   fixed and shuffled reps, 12 (4.8%) reach p ≤ 0.05.

One residue in the second pass's data: it ran with non-ASCII characters unescaped, so five of
289 labels (accented names, a pound sign) differed from the production artifact; none of the
244 picks across the fifteen reps landed on those five clusters, the rendering was identical
across arms, and the shipped code now reproduces the artifact byte for byte, with a test on a
non-ASCII label.

## Method (second pass, the data reported here)

`bin/eval-select-order` (`newsroom/src/eval_select_order.py`), the production agent-SDK path in
Docker, the real `.claude/agents/select.md` (Sonnet 4.6, thinking disabled), run 298's archived
`clusters.json` (289 clusters), `articles_1-5.csv`, `recap.txt`, `yesterday_headlines.txt`, plus a
`sources.csv`. For this run that file was built with jq from the whole catalogue, so it carried
the two parked sources (`the_hindu`, `upi`) that `prepare.py` drops, with every field quoted;
identical across all fifteen reps, so no arm effect, and the wrapper now writes it the way
`prepare.py` does. `weekly_recap.txt` is not archived and was absent for every rep. Inside a
rep's input directory every file other than `clusters.json` is byte-identical across the
fifteen reps; the permutation is recorded beside the directory, not in it. Rep i of every arm
ran before rep i+1 of any, concurrency 2.

A rep's answer is mapped to ORIGINAL cluster indices by the story's citations
(`write_fanout.resolve_cluster_index`, the production rule), not by the positional
`cluster_index` it wrote, then back through the permutation. Measurements: mean pairwise
Jaccard within each arm and across each pair of arms, over all selected clusters and over
must_know alone. Six pairs of comparisons (three arm pairs, two metrics), each with the gap and
the shift test. All of it is `summarise()` in the harness and re-derives from
`docs/proposed/2026-09-16-select-order/run298/summary.json` with
`bin/eval-select-order rescore <that file>`, no model call; the cluster sizes, labels and
read positions quoted here come from `clusters.json` and `permutations/` beside it.

**Negative control (no model call, `--check`):** the archived `selected.json` pushed through a
permutation as if a model had answered in the permuted space canonicalises to the same 16
clusters as the unpermuted file, where the unpermuted side is read without the inverse
mapping so a broken inverse cannot cancel out of both sides (a unit test breaks the inverse
and requires the check to fail; another requires it to fail on an empty archived answer).
Recorded cost from the SDK: $6.24 for fifteen reps ($0.42 a call); wall clock about 20
minutes, not recorded in the log.

## Result

| | fixed | shuffled | sorted |
|---|---|---|---|
| picks per rep | 17, 16, 16, 17, 16 | 17, 17, 16, 17, 16 | 14, 17, 16, 16, 16 |
| all-picks Jaccard, mean [min, max] | **0.514** [0.42, 0.60] | **0.467** [0.32, 0.57] | **0.554** [0.39, 0.78] |
| must_know Jaccard, mean | 0.512 | 0.464 | 0.750 |
| clusters picked by all 5 reps | 7 | 4 | 7 |
| clusters picked by any rep | 29 | 29 | 28 |
| mean size of a picked cluster (articles) | 8.8 | 9.0 | 9.9 |
| singleton clusters picked | 7 of 82 | 3 of 83 | 2 of 79 |
| written cluster_index disagreeing with citations, per rep | 3, 8, 0, 3, 5 | 0, 0, 0, 0, 12 | 0, 0, 0, 0, 0 |
| cost | $1.95 | $2.10 | $2.19 |

Cross-arm means: fixed vs shuffled 0.43, fixed vs sorted 0.50, shuffled vs sorted 0.47. Two
clusters were picked by all fifteen reps (Thaci's sentence, the Houthi drone near Mecca).

| comparison | metric | gap | two-sided p | gap needed for p ≤ 0.05 | shift | one-sided p |
|---|---|---|---|---|---|---|
| fixed vs shuffled | all | +0.047 | 0.36 | 0.072 | +0.063 | **0.008** |
| fixed vs shuffled | must_know | +0.048 | 0.72 | 0.182 | +0.023 | 0.31 |
| fixed vs sorted | all | −0.040 | 0.63 | 0.135 | +0.030 | 0.10 |
| fixed vs sorted | must_know | −0.238 | 0.11 | 0.290 | +0.070 | 0.09 |
| shuffled vs sorted | all | −0.087 | 0.33 | 0.158 | +0.040 | **0.048** |
| shuffled vs sorted | must_know | −0.286 | **0.016** | 0.275 | +0.138 | **0.032** |

**Reading.**

- Sampling noise is the first-order story. Identical reruns agree at about 0.5 in every
  order tried, roughly 11 shared clusters of the 22 in a pair's union. The 2026-09-03 figure
  of 0.24 to 0.34 (three reps on run 285) is lower; the gap is unexplained (a different day,
  n = 3, any prompt change since) and neither number is a constant.
- Order is a second-order but real term. The fixed and shuffled arms are about equally
  self-consistent and agree with each other less than with themselves: roughly one pick of 16
  (0.6 to 1.3) sits on one set of clusters when the list is in the archived order and on
  another when it is random. The two informative orders (archived, size-sorted) do not shift against each other.
  This is smaller than the paper's 16 to 34%, and the gap test had the power to see an effect
  that size (the largest gap any relabelling of these ten reps can produce is 0.084).
- The size-sorted arm is not worse than the archived order on any measure and is better on
  must_know agreement, the one gap comparison to clear 0.05 uncorrected, on one run, out of
  six. Suggestive, not a result.
- The positional `cluster_index` drift is a per-rep phenomenon, not an arm total: the
  archived order drifted in four of five reps, the other two orders in one of ten. An
  earlier draft read this as the model counting better when big clusters come first; the
  per-rep data refute that (four shuffled reps counted to position 285 with zero drift while
  the fixed arm, whose picks sat at the lowest read positions, drifted most). No mechanism is
  claimed. The citations decide in prod, so this is not reader-facing.

## What this changes

- **Order-averaging is a costed option, not a recommendation.** k permuted SELECT runs with a
  majority vote would remove the one-pick order component and, as a side effect, some of the
  sampling noise. At run 298's recorded $0.42 a call that is about $1.25 a run for k = 3
  against a $6 run, to stabilise roughly one should_know pick a day. Stability is not validity
  (`docs/2026-06-26-cluster-eval-noground-truth-literature.md`, strand 5): a vote favours
  consensus clusters and shrinks the should_know tail toward the conventional. Whether a
  reader wants a stabler tail or a more varied one is editorial; the price is the number
  above.
- **A canonical size-descending order is the free follow-up.** It is a sort in
  `cluster_extractjoin` before `clusters.json` is written, and this harness measures it
  directly. Before it ships it needs a second run on a busier day (the must_know 0.75 is five
  reps on one day and does not survive correction over six comparisons) and the same
  editorial question answered.
- The harness stays: three arms, both tests with unit tests, a negative control that refuses
  a vacuous pass, recorded cost, interleaved arms, production serialisation, a rescore mode,
  and guards that turn the ways it could have exited 0 while broken (no article rows,
  duplicate arms, an empty tier, an unknown arm, a half-finished fetch) into errors.

## Open

- Second run of the three arms on a busier day (run 291 or 292, ~700 articles) before either
  the sort or a vote is proposed for prod; `make eval-select-order RUN=291` runs all three.
- The per-rep drift pattern is free to re-measure on that run; it is recorded, not explained.
