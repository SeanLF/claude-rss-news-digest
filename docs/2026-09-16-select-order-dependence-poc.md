# SELECT order dependence: none of the paper's size on the pick; one must_know signal to re-run (2026-09-16)

Follow-up to `docs/2026-09-16-sota-and-competitor-recheck.md` §1.1, which asked one question of
[arXiv 2608.26762](https://arxiv.org/abs/2608.26762) (order alone moves an LLM scorer's retained
set by 16 to 34%): does that transfer to our SELECT stage, or was the rep-to-rep instability
recorded on 2026-09-03 (Jaccard 0.24 to 0.34) all sampling noise?

## TL;DR

**No order effect of the paper's size on the selected set. The first pass that said otherwise
had two harness defects.** On run 298, five reps of the shipped `select.md` per arm:

| arm | cluster order the model read | within-arm Jaccard, all picks | must_know |
|---|---|---|---|
| fixed | as archived | 0.51 | 0.51 |
| shuffled | a fresh uniform permutation per rep | 0.47 | 0.46 |
| sorted | size-descending | 0.55 | 0.75 |

On all picks, no pair differs beyond what relabelling the reps produces (two-sided exact p 0.33
to 0.63), and five reps per arm could have seen a paper-sized effect: the smallest fixed vs
shuffled gap that reaches p ≤ 0.05 is 0.07, and a 16% set movement from order alone would show as a gap of
about 0.2. Reruns in the SAME order already disagree on half their picks, so order-averaging
over permutations, the paper's fix, has nothing to average away here and is not a PoC
candidate. The one contrast of the paper's magnitude is on the must_know tier: sorted 0.75 vs
shuffled 0.46 (two-sided p = 0.016, one of six comparisons; Bonferroni over six gives 0.095).
That is a second run on a busier day, not a result.

## The first pass, and why it is superseded

The first run of the harness reported fixed 0.51 vs shuffled 0.385 (one-sided exact p = 0.028,
two-sided 0.056) and this doc said "order is a real component". Adversarial review found:

1. **Arm confounded with time.** The five fixed reps all ran first, the five shuffled reps
   all ran afterwards (file mtimes 10:52 to 10:54 vs 10:55 to 11:00), so any drift in the
   backend between those windows was indistinguishable from the arm.
2. **Not the production file.** The harness rewrote `clusters.json` compactly (one line,
   31 KB) where production writes `indent=2` (2120 lines, 46 KB), so even the "fixed" arm
   did not read what SELECT reads in prod.
3. **The archived order is not neutral.** `join_tags` emits clusters in the order of their
   first article id, which front-loads big clusters: all eleven size-10+ clusters sit in the
   first 105 of 289. Inside the shuffled arm the clusters of five or more articles that got
   picked sat at a mean read position of 145 and the unpicked ones at 147 (uniform 144), so
   there was no position preference to see. Even had the gap held, it would have said "the
   join's accidental order carries signal", not "SELECT prefers positions".

Both harness defects were fixed (reps interleaved round-robin across arms, `rep_order`, with a
test; `clusters.json` serialised byte-for-byte as `cluster_extractjoin` does, with a test on a
non-ASCII label) and a third arm added to test the reading in point 3 directly. One residue:
the second pass ran with `indent=2` but non-ASCII characters unescaped, so five of 289 labels
(accented names, a pound sign) differed from the production artifact; none of the 244 picks
across the fifteen reps landed on those five clusters, the rendering was identical across
arms, and the shipped code now reproduces the artifact byte for byte. The positions quoted in
point 3 are from the first pass's shuffled reps; the second pass gives 142 and 149. The first
pass's outputs are kept in `scratch/select-order/run298/work-v1-compact-json/` and are not
quoted further.

## Method (second pass)

`bin/eval-select-order` (`newsroom/src/eval_select_order.py`), the production agent-SDK path in
Docker, the real `.claude/agents/select.md` (Sonnet 4.6, thinking disabled), run 298's archived
`clusters.json` (289 clusters), `articles_1-5.csv`, `recap.txt`, `yesterday_headlines.txt`, plus a
`sources.csv`. For this run that file was built with jq from the whole catalogue, so it carried
the two parked sources (`the_hindu`, `upi`) that `prepare.py` drops, with every field quoted;
identical across all fifteen reps, so no arm effect, and the wrapper now writes it the way
`prepare.py` does. `weekly_recap.txt` is not archived and was absent for every rep. Every
input other than the order of the `clusters` array is byte-identical across all fifteen reps.
Rep i of every arm ran before rep i+1 of any, concurrency 2.

A rep's answer is mapped to ORIGINAL cluster indices by the story's citations
(`write_fanout.resolve_cluster_index`, the production rule), not by the positional
`cluster_index` it wrote, then back through the permutation. Measurement: mean pairwise Jaccard
within each arm over all selected clusters, and over must_know alone. Significance: pool the
reps of two arms and enumerate all 252 five/five splits, counting those whose within-group
agreement gap is at least the observed one in absolute value (two-sided). Six comparisons
were made (three pairs, two metrics).

**Negative control (no model call, `--check`):** the archived `selected.json` pushed through a
permutation as if a model had answered in the permuted space canonicalises to the same 16
clusters as the unpermuted file, where the unpermuted side is read without the inverse
mapping so a broken inverse cannot cancel out of both sides (a unit test breaks the inverse
and requires the check to fail; another requires it to fail on an empty archived answer).
Recorded cost from the SDK: $6.24 for fifteen reps, 21 minutes.

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

| comparison | all-picks gap | two-sided p | smallest gap with p ≤ 0.05 | must_know gap | two-sided p |
|---|---|---|---|---|---|
| fixed vs shuffled | +0.047 | 0.36 | 0.072 | +0.05 | 0.72 |
| fixed vs sorted | −0.040 | 0.63 | 0.135 | −0.24 | 0.11 |
| shuffled vs sorted | −0.087 | 0.33 | 0.158 | −0.29 | **0.016** |

**Reading.**

- Sampling noise is the first-order story. Identical reruns agree at about 0.5 in every
  order tried, roughly 11 shared clusters of the 22 in a pair's union. The 2026-09-03 figure
  of 0.24 to 0.34 (three reps on run 285) is lower; the gap is unexplained (a different day,
  n = 3, any prompt change since) and neither number is a constant.
- The first pass's fixed-vs-shuffled gap of 0.125 became 0.047 once arms were interleaved
  and the file took production's shape. That is consistent with the first gap being the time
  confound. The run had the power to see the paper's effect: at 16 picks a rep and a
  baseline agreement of 0.51, a 16% set movement from order alone would appear as a gap of
  about 0.2 against a significance threshold of 0.07, and the largest gap any relabelling of these
  ten reps can produce is 0.084. What five reps cannot see is anything under about 0.07,
  roughly a 6% set movement.
- The size-sorted arm is not worse than the archived order on any measure and is better on
  must_know agreement, the one comparison to clear 0.05 uncorrected, on one run, out of six.
  Suggestive, not a result.
- The positional `cluster_index` drift is a per-rep phenomenon, not an arm total: the
  archived order drifted in four of five reps, the other two orders in one of ten. The
  first draft read this as the model counting better when big clusters come first; that is
  refuted by the per-rep data (four shuffled reps counted to position 285 with zero drift
  while the fixed arm, whose picks sat at the lowest read positions, drifted most). No
  mechanism is claimed. The citations decide in prod, so this is not reader-facing.

## What this changes

- **Order-averaging is off the list.** The recheck doc's "worth a costed PoC if the shuffled
  arm is materially worse" condition was not met on the clean run, at a power that would
  have shown the paper's effect.
- **A canonical size-descending order is the one cheap follow-up.** It is a sort in
  `cluster_extractjoin` before `clusters.json` is written, costs nothing per run, and this
  harness measures it directly. Before it ships it needs a second run on a busier day (the
  must_know 0.75 is five reps on one day and does not survive correction over the six
  comparisons made) and the reader-facing question answered: a stabler must_know tier is
  only a gain if the stable picks are the right ones, and stability is not validity
  (`docs/2026-06-26-cluster-eval-noground-truth-literature.md`, strand 5).
- The harness stays: three arms, a negative control that refuses a vacuous pass, recorded
  cost, interleaved arms, production serialisation, and guards that turn the ways it could
  have exited 0 while broken (no article rows, duplicate arms, an empty tier, an unknown arm)
  into errors.

## Open

- Second run of the three arms on a busier day (run 291 or 292, ~700 articles) before the
  sort is proposed for prod; `make eval-select-order RUN=291` runs all three.
- The per-rep drift pattern is free to re-measure on that run; it is recorded, not explained.
