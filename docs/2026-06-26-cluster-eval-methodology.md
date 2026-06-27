# CLUSTER evaluation methodology — is ARI-vs-gold the right ruler? (2026-06-26)

Prompted by a sharp question after a day of scoring clustering methods by ARI vs a
single Sonnet `clusters.json`: **is that metric even valid for an ill-posed problem?**
Three-strand recent-literature sweep (news-clustering eval surveys; evaluation under
ambiguous ground truth; LLM-as-judge + task-grounded eval). Verdict: **no — ARI-vs-one-gold
is a known-weak ruler here, and it silently set the bar for every "doesn't work" conclusion
today.** This doc records where the field stands and the corrected protocol. Empirical band
numbers appended once measured.

## The three problems with ARI-vs-gold (all confirmed by the literature)

1. **The "gold" is one Sonnet sample, not truth.** We score against a single production
   clustering; Sonnet vs its own re-run is only ~0.75 ARI. Scoring Sonnet against Sonnet is
   the same circularity the eval-floor work flagged for COHERENCE. No survey treats
   single-reference news clustering as a well-posed evaluation problem
   ([USTORY concedes "true story labels are not readily available"](https://arxiv.org/abs/2304.04099)).
2. **The task is genuinely ill-posed.** "Congressional vote on a deal" vs "diplomatic
   reaction to the same deal" — one story or two is a defensible judgment (the documented
   "quasi-identity / event granularity" problem,
   [arXiv 2109.05250](https://arxiv.org/abs/2109.05250)). ARI punishes *valid-but-different*
   identically to *wrong*.
3. **ARI measures partition geometry, not what the digest needs.** It is symmetric and
   weights all articles equally, but the digest cares about ONE asymmetry: two same-story
   articles split apart → the reader sees a duplicate. Over-merging two distinct stories is
   a different, often cheaper error. Intrinsic metrics show only moderate-to-negligible
   correlation with downstream task utility
   ([intrinsic↔extrinsic divergence](https://www.emergentmind.com/topics/extrinsic-evaluation-methods)).

## Where the field stands (recent literature)

- **News-story clustering has moved to online/streaming discovery** (USTORY SIGIR'23
  [2304.04099](https://arxiv.org/abs/2304.04099); DenStream narrative tracking
  [2601.20680](https://arxiv.org/abs/2601.20680)), but still scores against a **single gold
  partition with B³/ARI/AMI**. The movement is toward **multi-metric + human-in-the-loop**
  validation, NOT a multi-reference benchmark — i.e. the field shares our unease but hasn't
  formalized a fix.
- **A standalone LLM is ~the cheap baseline, not SOTA, on the classic benchmark.** On ECB+
  cross-document event coreference: standalone GPT-4 = 76.8 CoNLL F1 (≈ lemma baseline 76.5);
  the LLM+small-model hybrid = 86.7 ([Synergetic 2024](https://arxiv.org/html/2406.02148v1)).
  LLMs help as a representation/summarization component, not as a standalone clusterer.
- **Metric critiques exist**: B³ over-credits singletons (ELM correction,
  [SIGIR'22](https://dl.acm.org/doi/10.1145/3539813.3545121)); token-matching F1 misses
  semantics (SEOE proposes LLM-judged semantic F1,
  [2503.03303](https://arxiv.org/pdf/2503.03303)).

## The corrected protocol (what we'll use)

### 1. Measure the intrinsic-ambiguity BAND, don't chase a point
Generate N≥5 independent Sonnet clusterings of the SAME articles; compute pairwise BCubed-F
(and ARI) **among the references**. That band is the achievable ceiling and the headline
context. A candidate is "at Sonnet level" when its agreement with the references is
**statistically indistinguishable from the references' agreement with each other** — not
when it beats a point ([HUME, arXiv 2510.10062](https://arxiv.org/html/2510.10062): low
inter-annotator ARI "indicates fundamental task ambiguity rather than human limitation";
human-human clustering agreement typically 0.55–0.78). **Our ~0.75 self-ARI is the first
data point of this band — beating it is overfitting to one draft's granularity choices.**

### 2. Primary metric: BCubed-F (not ARI)
BCubed is the only extrinsic metric satisfying all four Amigó (2009) constraints
([Discover Computing](https://link.springer.com/article/10.1007/s10791-008-9066-8)); two are
decisive for our singleton-heavy 160-clusters/241-articles distribution — **Size-vs-Quantity**
(a small error in a big cluster beats many errors in small clusters) and **Rag Bag** (dump
noise into a coarse cluster rather than punish it). ARI is dominated by true-negative
non-pairs and is cluster-count sensitive; keep it + **AMI** only as chance-corrected
secondaries. **Always report all-singletons and all-one-cluster floors** (BCubed gives
singletons self-credit — [van Heusden 2022](https://irlab.science.uva.nl/wp-content/papercite-data/pdf/van-2022-bcubed.pdf)).
Mirror coreference practice: report multiple complementary metrics, never rank on one point.

### 3. Task-grounded metric: published duplicate-violation rate (the real objective)
Score the *digest*, not the partition. Over the FINAL selected stories: embedding-cosine to
surface candidate same-event pairs → LLM "same event? yes/no/either" judge on candidates
only → report duplicates-shown-per-digest. Gold-free, measures the reader-facing failure,
and correctly ignores harmless over-merges in the unselected tail
([news dedup](https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/articles-deduplication);
exact match has ~10% recall so semantic/LLM judging is required,
[2210.04261](https://arxiv.org/html/2210.04261v2)). Coverage guardrail: subtopic-recall of
the must-know events so an over-merger that *hides* a distinct story is also caught.

### 4. Boundary-pair adjudication replaces full-partition scoring
Judge only the *disagreement* pairs (where references/methods differ), with a **3-way
same / different / either-defensible** label, **order-swapped** (position bias flips verdicts
17–40%, [2406.07791](https://arxiv.org/html/2406.07791v3)), by a **cross-family judge** (NOT
the clustering model — self-preference/circularity; our COHERENCE golden already hit this).
Error rate = fraction of *decided* (non-"either") pairs wrong; "either" excluded from the
denominator. **Caveat to respect:** the LLM same/different oracle is *least* accurate exactly
on low-confidence boundary pairs ([few-shot clustering, 2307.00524](https://arxiv.org/html/2307.00524):
Tweet 89.7% but entity/intent ~55–57%), so the tie option and order-swap (a free uncertainty
signal) are mandatory, and we trust **paired direction, not the absolute scalar** (same lesson
as the RECAP Haiku eval).

## Why this matters for today's conclusions

Every "X doesn't work" today was "X scores lower ARI vs one gold." The *mechanistic* findings
survive (refine-output-ARI tracks draft-ARI; Haiku extraction reads clean; NIM throttling).
But the *absolute "below the bar"* verdicts — draft-injection "failed," extract-join "capped
at 0.68" — are built on the weak ruler. If extract-join's BCubed-F lands **inside** the
Sonnet-vs-Sonnet band, that conclusion is overturned: it would be Sonnet-indistinguishable,
just cheaper. The band experiment (running) decides this.

## Empirical results — run 204 (band_eval.py, 6 fresh Sonnet refs + archived gold)

**The intrinsic ambiguity band (7 Sonnet clusterings of the SAME 241 articles, 21 pairs):**

| | mean | sd | range |
|---|--:|--:|--:|
| BCubed-F (Sonnet vs Sonnet) | 0.933 | 0.012 | [0.910, 0.963] |
| ARI (Sonnet vs Sonnet) | 0.704 | 0.057 | [0.603, 0.878] |

**Sonnet does not agree with itself better than ~0.70 ARI** (0.60–0.88 run-to-run). The
"0.75 bar" used all day was *inside Sonnet's own noise* — every cheap method ranked "below
it" was within Sonnet's self-disagreement. ARI differences of 0.05–0.07 between methods were
noise.

**Methods re-scored on BCubed-F vs the reference band:**

| method | BCubed-F | ±sd | BCP | BCR | ARI | verdict |
|---|--:|--:|--:|--:|--:|---|
| refine: draft-gold | 0.941 | .014 | .953 | .930 | 0.746 | in-band (center) |
| refine: draft-deepseek | 0.918 | .013 | .944 | .893 | 0.669 | in-band |
| extract-join (Haiku+tfidf) | 0.913 | .011 | .942 | .887 | 0.661 | in-band (edge) |
| refine: draft-embed | 0.913 | .007 | .899 | .928 | 0.670 | in-band (edge) |
| draft_deepseek (chunked) | 0.911 | .012 | .935 | .888 | 0.648 | in-band (edge) |
| draft_embed (raw Qwen) | 0.906 | .010 | .947 | .868 | 0.629 | 0.004 below |
| [all-singletons] | 0.804 | .009 | 1.00 | .673 | 0.000 | below |
| [all-one-cluster] | 0.022 | .002 | .011 | 1.00 | 0.000 | below |

**Finding: the cost-lever "dead ends" were largely metric artifacts.** On BCubed-F vs the
Sonnet band, the cheap methods (extract-join 0.913, DeepSeek draft 0.911, embedding-refine
0.913, raw embedding 0.906) are all at/just-inside the band [0.910, 0.963]. The "extract-join
caps at 0.68 ARI, below the 0.75 bar" verdict is overturned — its ARI 0.661 is squarely in
Sonnet's own 0.60–0.88 ARI range.

**Honest caveats (do not oversell):**
- **BCubed is inflated by the singleton-heavy distribution** (all-singletons floor = 0.804),
  so the *discriminative* band is a narrow [0.80→0.93]. The cheap methods sit at the **low
  edge** of the Sonnet band (~1.5 sd below its 0.933 mean), i.e. *near*-Sonnet, not identical;
  only `draft-gold` (0.941) is band-center. So degenerate baselines are clearly excluded (good
  — the metric discriminates), but the cheap-vs-Sonnet difference is small, not zero.
- One run (204); needs 205 to confirm generalization.
- **BCubed-in-band is necessary, not sufficient.** It says "as Sonnet-like as Sonnet is to
  itself on partition agreement," NOT "produces an equally good digest." The decisive tests
  remain: (a) are the residual disagreements real errors or defensible differences
  (cross-family boundary adjudication), and (b) does a cheap-clustered digest have more
  duplicate-violations (task-grounded). _(running)_

## Empirical results — boundary adjudication (run 204, adjudicate.py)

Are extract-join's disagreements with Sonnet REAL errors or DEFENSIBLE differences? Sonnet
pair-consensus from the 7 references; a **cross-family** judge (GLM-5.1 via NIM — not
Anthropic, avoids self-preference) ruled 40 pairs BLIND, **order-swapped** (verdict flip →
"either"), 3-way same/different/either.

First, the scale: across ~29,000 article pairs, extract-join has only **12 confident
under-merges + 13 confident over-merges** vs the Sonnet consensus (plus 97 intrinsically
ambiguous pairs where Sonnet's own runs split). Tiny disagreement surface.

| category | n | same | different | either | reading |
|---|--:|--:|--:|--:|---|
| **AMBIG** (Sonnet refs split — control) | 15 | 1 | 1 | 13 | **87% "either" → method validated**: judge correctly flags the ill-posed middle, isn't trigger-happy |
| **UNDER** (Sonnet merges, EJ splits) | 12 | 3 | 5 | 4 | judge backs EJ (diff/either) **75%** — only 3 genuine missed-merges |
| **OVER** (Sonnet splits, EJ merges) | 13 | 2 | 9 | 2 | judge backs Sonnet **69%** — **9 genuine wrong-merges** |

**Findings:**
1. **The control validates the protocol.** 87% "either" on Sonnet's own split-pairs (vs only
   4/12 and 2/13 "either" on the real disagreements) shows the judge+order-swap correctly
   separates genuine ambiguity from decidable calls — it isn't defaulting to "either."
2. **Extract-join's lower RECALL is mostly NOT error.** When it split what Sonnet merged, the
   independent judge agreed/called-defensible 75% of the time — **Sonnet over-lumps**, and the
   recall gap that ARI/BCubed punished hardest is largely the metric penalizing defensible
   sub-story distinctions. This is the ill-posedness made concrete.
3. **But extract-join has a REAL, localized precision defect.** Its over-merges are genuine
   wrong-merges 69% of the time — the TF-IDF-on-entity-bag join lumps distinct stories that
   share entities ("Trump + Congress" spans several different stories). ~9 genuine
   wrong-merges on the whole run; small and concentrated (≈1–2 junk-drawer clusters), and
   fixable (entity+event *conjunction* join, or a learned classifier, or a thin Sonnet refine).
4. **Net: cheap clustering and Sonnet are peers, not master/apprentice.** Both make a handful
   of defensible-and-real errors at similar rates; ~12 genuine pair-errors for extract-join
   across ~29k pairs. The "editorial-judgment wall" claimed earlier was half-right — Sonnet
   makes finer distinctions, but *also over-lumps*, and the cheap method's residual real defect
   is a small fixable precision issue, not a fundamental cap.

## Generalization — run 205 (465 articles, partial band: 3 fresh refs + gold)

Confirms 204 on a larger, noisier run. Sonnet self-agreement is *even lower* at scale:
**band BCubed-F [0.821, 0.884], ARI [0.554, 0.684]** (mean ARI 0.627). The cheap embedding
methods are **comfortably in-band, not just at the edge**: draft-embed-refine BCubed-F 0.845,
raw embedding 0.832 (band [0.821, 0.884]); all-singletons 0.719 clearly excluded. (Only the
embedding methods were available for 205 — Haiku extraction wasn't run there — but the band
finding generalizes cleanly.) The 205 reference runs cost $1.2–2.4 and 11–25 min EACH, so the
full 6-ref band was cut after 3 (the point was already made) — a deliberate budget call.

## Task-grounded test — the real ruler (run 204, run_downstream.py + judge_digests.py)

Ran the actual SELECT→WRITE stages on the **extract-join** clustering (cost $0.65: SELECT
$0.39 + WRITE $0.26) and compared the PUBLISHED digest to run 204's archived Sonnet digest.
Only the ~16 selected stories reach the reader, so this scores the real objective. Judge =
GLM-5.1 (NIM), gold-free.

| axis | Sonnet digest | extract-join digest |
|---|--:|--:|
| stories published | 16 (5 must + 11 should) | 16 (4 must + 12 should) |
| **internal duplicate-groups** (under-merge risk) | **2** | **1** |
| stories diverging from the other | — | 6/16 |

**Findings:**
1. **No duplication regression — the under-merge risk did not materialize.** The cheap digest
   had *fewer* internal duplicates than Sonnet's (1 vs 2); both are borderline G7-summit-facet
   repeats within judge noise. This is the clean, un-confounded result (within-digest measure).
2. **No catastrophic miss.** The 6 "missed" Sonnet stories are largely covered by *related*
   cheap stories on the same thread (Sonnet "Iran deal kept from Congress" ↔ cheap "Senate
   blocks war-powers challenge to Trump's Iran campaign"; both cover G7 and Iran-nuclear from
   different angles). Different framing, not a hole.
3. **Confound (flagged honestly):** the cross-coverage divergence (6/16) compares cheap-now vs
   the *archived* Sonnet digest, conflating clustering difference with **SELECT's own
   run-to-run non-determinism** (SELECT is also a stochastic Sonnet call) and temporal context.
   So 6/16 is an *upper bound* on the clustering effect, consistent with the ~30% Sonnet-vs-
   Sonnet divergence the band showed. **Clean control (deferred, session-limited): a fresh
   SELECT→WRITE on a Sonnet clustering, to isolate clustering from SELECT variance (~$0.65).**

**Net:** the cheap-clustered digest is a serviceable, shippable digest — no duplication
regression, no missing major story, comparable size — differing only within the range of
Sonnet's own run-to-run editorial variation. The task-grounded ruler **confirms** the band +
adjudication finding at the product level.

## SELECT-variance control — run 204 (the deferred clean control, now run)

The 6/16 cross-coverage divergence above conflated *clustering difference* with *SELECT's
own run-to-run non-determinism*. To isolate them, ran a fresh SELECT→WRITE on a **Sonnet**
clustering (`out/refs/ref-204-0.json`, `--tag sonnetctrl`, $0.65) and judged it against the
SAME archived Sonnet digest the cheap one was judged against. If a fresh-SELECT-on-Sonnet
digest diverges from the archive as much as the cheap one does, the divergence is SELECT, not
clustering.

| vs archived Sonnet digest | extract-join (cheap cluster) | **sonnetctrl (Sonnet cluster, fresh SELECT)** |
|---|--:|--:|
| stories published | 16 | **21** |
| internal duplicate-groups | 1 | 1 |
| Sonnet stories MISSED | 6/16 | **4/16** |
| novel vs archived | 6 | **8** |

**Finding: the cross-coverage divergence is overwhelmingly SELECT variance, not clustering.**
Re-running SELECT on a *Sonnet* clustering already drops 4/16 archived stories, adds 8 novel,
and even picks a different story COUNT (21 vs 16) — SELECT freely re-chooses tier depth and
the should-know tail each run. The cheap clustering's 6/16 "missed" is therefore ~4/16 SELECT
noise + only ~2/16 attributable to clustering — inside Sonnet's own editorial variance. The
under-merge objective (internal duplicates) is **identical** (1) for cheap and Sonnet-control,
both *below* the archive's 2. This **removes the last confound** flagged above: the published
cheap digest differs from Sonnet's only as much as Sonnet differs from itself.

## Over-merge fix attempt — entity+event CONJUNCTION join (run 204)

The adjudication isolated extract-join's one real defect: ~9 genuine wrong-merges where the
additive TF-IDF entity-bag (entities ×3) lets shared entities ALONE clear the merge threshold.
Tested the obvious fix (`join_conjunction.py`): compute event-phrase embedding-cosine and
entity Jaccard *separately* and require BOTH (conjunction), instead of one blended cosine.
Three formulations swept; the entity-GATED event-similarity (`event_cos` gated on Jaccard≥0.10)
won on BCubed-F and was materialized.

| | BCubed-F | BCP | BCR | ARI | band verdict |
|---|--:|--:|--:|--:|---|
| extract-join (TF-IDF, additive) | 0.913 | 0.942 | 0.887 | 0.661 | in-band |
| **extract-join (conjunction)** | **0.901** | **0.967** | **0.844** | 0.578 | **0.008 below band** |

Boundary adjudication (cross-family GLM-5.1, order-swapped), conjunction vs the original:

| category | TF-IDF (genuine errors) | conjunction (genuine errors) |
|---|--:|--:|
| OVER (wrong-merges) | 9/13 | **8/11** |
| UNDER (missed-merges) | 3/12 | **8/15** (disagreement surface 12→**32** pairs) |

**Finding: the conjunction is the WRONG lever — it does not reach band-center.** It raises
pair-PRECISION to 0.967 (highest of any real method — over-merges *do* drop at the pair level)
but at a recall cost (0.887→0.844) that pushes net BCubed-F *out* of the band, and barely dents
the genuine over-merges (9→8) while sharply *increasing* genuine under-merges (the entity gate
also blocks valid merges where Haiku's entity strings drifted across extraction batches).
**Why:** the surviving wrong-merges are NOT "shared entity, different event" lumps (which a
gate catches) — they are semantically-close DISTINCT SUB-EVENTS that share entities AND have
similar event-embeddings: *G7 trusted-partners AI-chip access* ↔ *US-China AI race bystanders*;
*Trump's Iran war weighs on G7 economies* ↔ *long-term implications of the US-Iran deal*. These
pass both the entity gate and the event-similarity, so no entity-based conjunction can separate
them. This is the **quasi-identity / event-granularity** problem, not an entity-bag artifact.
The residual precision defect needs finer event discrimination (a thin Sonnet/LLM refine on the
~8–11 borderline pairs, or a learned same-event classifier), NOT a cheaper deterministic gate.
The entity-conjunction sub-lever is **closed**.

## Revised conclusion

The day's "cheap clustering is dead / caps below the bar" verdicts were **substantially a
metric artifact**. On the literature's metric (BCubed-F) against the proper ceiling (the
Sonnet-vs-Sonnet band), and under cross-family boundary adjudication, a free/cheap extract-join
clustering is **near-Sonnet** — its recall "gap" is mostly defensible difference, and its one
real weakness (entity-bag over-merging) is small (~8 genuine wrong-merges/run). **The CLUSTER
cost lever is NOT dead; it was killed by ARI-vs-one-gold.** Both remaining tests are now in:
(1) the task-grounded SELECT-variance control showed the published-digest divergence is
overwhelmingly SELECT noise, not clustering — the cheap digest is shippable; (2) the cheap
entity-conjunction over-merge fix FAILED (the residual wrong-merges are a granularity problem,
not an entity-bag artifact) — closing that defect needs a thin LLM refine on the handful of
borderline pairs, not a cheaper gate. The cheap-clustering-then-thin-Sonnet-refine-on-borderline
architecture (the prior-art convergent design) remains the open, promising path.

## De-biased re-score (2026-06-27) — pairwise-link F1 corrects the BCubed overstatement

An adversarial lit check (van Heusden 2022 "BCubed Revisited / ELM"; Amigó 2009) confirmed
**BCubed-F is inflated on our singleton-heavy partitions** — singletons get full self-credit,
so the metric compresses near 1.0 (all-singletons floor 0.804) and the discriminative signal
lives in the non-singleton minority the average dilutes. So "cheap methods are IN-BAND on
BCubed-F" above is **partly a metric artifact**. Re-ran the SAME band experiment on
**pairwise-link F1** (precision/recall over co-clustered article *pairs*; singletons form no
pairs → no self-credit → the floor collapses to ~0 and the range opens up).
Harness: `scratch/cluster-replay/band_eval_pairwise.py` (reuses `band_eval` loaders +
`analyze.pair_prf`, both deflation-checked; degenerate self-checks gate every run).

| | Sonnet self-band (mean / range) | best cheap method | z (sd below mean) | verdict |
|---|---|---|--:|---|
| **run 204** (7 refs) | 0.706 / [0.607, 0.879] | extract-join 0.663 | **−0.8** | in-band, low edge |
| **run 205** (4 refs) | 0.629 / [0.557, 0.686] | refine-embed 0.536 | **−2.1** | **below band** |

(all-singletons → pairwise-F1 0.000, all-one-cluster → 0.014: the de-biasing is real. Recovered
fraction = cheap/Sonnet-self-agreement: ~0.94 on 204, ~0.77–0.85 on 205. Per-gold pairwise-F1
reconciles with the draft-refine doc, e.g. draft-embed 0.672 here vs 0.665 there.)

**Corrected conclusion.** The reversal's *core* survives — cheap methods are NOT degenerate
(degenerate baselines collapse to ~0) and recover ~94% of Sonnet's own (low, 0.71) pairwise
self-consistency on a good day; the ARI-vs-one-gold critique still holds. But the **"near-Sonnet
/ indistinguishable" framing is overturned**: on the honest metric the gap is a real ~0.6–0.8 sd
on 204 and **opens to ~2.1 sd (below Sonnet's own band) at 465-article scale (205)**. BCubed's
"comfortably in-band" for 205 was singleton inflation. A *pure* cheap swap therefore ships
measurably worse clustering on big news days — not free. This **sharpens the engineering
target**: only `draft-gold` (z=+0.7) reaches Sonnet level, and even Sonnet-full-refine-on-a-cheap-
draft anchors to the draft (~0.67/0.54). The one untested path — cheap clustering → thin-Sonnet-
refine **on the ~20 borderline pairs only** (the prior-art arch, NOT a full re-cluster) — is
precisely aimed at this gap, since adjudication localized the real errors to ~9 over + ~12 under
merges across ~29k pairs. That is the experiment worth running next.

## Key sources
van Heusden 2022 BCubed Revisited/ELM https://dl.acm.org/doi/10.1145/3539813.3545121 ·
HUME (band/ceiling) https://arxiv.org/html/2510.10062 · Amigó 2009 (BCubed constraints)
https://link.springer.com/article/10.1007/s10791-008-9066-8 · BCubed Revisited/ELM
https://dl.acm.org/doi/10.1145/3539813.3545121 · USTORY https://arxiv.org/abs/2304.04099 ·
Dealing with Disagreements (TACL) https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00449/109286 ·
few-shot clustering LLM-oracle https://arxiv.org/html/2307.00524 · position bias
https://arxiv.org/html/2406.07791v3 · intrinsic↔extrinsic divergence
https://www.emergentmind.com/topics/extrinsic-evaluation-methods · news dedup
https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/articles-deduplication ·
ClusterLLM (triplet judging) https://arxiv.org/abs/2305.14871 · CDCR quasi-identity
https://arxiv.org/abs/2109.05250
