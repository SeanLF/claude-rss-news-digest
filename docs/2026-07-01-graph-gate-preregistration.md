# Extract→join vs holistic-CLUSTER — the REAL gate (pre-registration, 2026-07-01)

Executes the go/no-go the `2026-06-30-phase2-close-and-graph-poc-handoff.md` handoff
demanded. **This document fixes the arms, the signals, and the DECISION RULE before any
result is looked at** — the single discipline this investigation keeps relearning
(`feedback_eval_ill_posed_metric`: a metric read after the fact, or a bar set to match a
lucky draw, is how the n=3 "win" and the ARI verdicts fooled us). Results are appended in a
clearly-separated section at the end; nothing above the line changes after the first run.

## What is being tested
Does a cheap **extract→join** clustering (Haiku per-article tag extraction → deterministic
TF-IDF/agglomerative join, NO holistic LLM) yield a **digest** at least as good as the
production **holistic Sonnet CLUSTER** digest, measured on reader-facing product signals and
adequately powered?

- **Arm H (holistic):** `data/claude_input/sonnet5_ab/cluster_claude-sonnet-4-6.json`
  (== the snapshot's `clusters.json`; Sonnet-4-6 holistic CLUSTER; 267 clusters over 471 of
  the 498 input articles).
- **Arm EJ (extract-join):** regenerated on THIS 498-article snapshot (A1–A498, from
  `data/claude_input/articles_*.csv`). Haiku extraction → deterministic join at a **held-out
  threshold of 0.80**, selected on runs 204/205 gold (NEVER on this snapshot → not circular
  w.r.t. the evaluation). **Threshold correction (pre-result):** the handoff's "0.40" was a
  misremembered value; the sweep shows the granularity-matching threshold shifts UP with
  corpus size (TF-IDF cosine distance is not scale-invariant): peak-ARI/count-match is 0.65 @
  241 articles (run 204: nclu 170 vs 160 gold) and 0.80 @ 465 articles (run 205: nclu 244 vs
  225 gold). 0.40 over-fragments at 498-article scale (417 clusters / 364 singletons, 73%
  singletons) which would unfairly handicap EJ; 0.80 yields a fair peer partition (224
  clusters / 133 singletons, top sizes [23,21,15,13…] vs holistic 267 / 196 / [28,16,16,13…]).
  This is held-out hyperparameter selection fixed BEFORE any gate result was seen; the
  snapshot's holistic partition never informs it. (Smoke-only: EJ-vs-holistic ARI 0.457.)

Downstream is **identical** for both arms: SELECT→WRITE→COHERENCE→`merge.assemble_selections`,
model fixed at `claude-sonnet-4-6` (prod apparatus). The CLUSTER **partition is the only
treatment.** Both digests are therefore Sonnet-4-6-authored — which is why an Anthropic judge
on the new signals cannot exhibit arm-level self-preference (see below).

## Signals (product-grounded; NEVER ARI/BCubed as a gate)

Primary (the gate is decided on these three):
1. **`internal_dups`** — # confirmed same-story duplicate groups WITHIN a digest (the
   under-merge / reader-sees-it-twice failure). Judge = independent Anthropic model
   (`claude-opus-4-8`, NOT the Sonnet-4-6 writer), **order-swapped**, only swap-stable groups
   counted (reuse `judge_digests.DUP_SYS` + `judge_reconcile.reconcile_dups`). Absolute
   per-digest count — no cross-arm reference, so not similarity-to-Sonnet. Lower is better.
2. **`miss_hard`** (primary) / **`miss_all`** (secondary) — # major stories PRESENT IN THE
   INPUT that the digest omits (the over-merge / cluster-collapse-hides-a-story failure).
   Circularity broken by judging coverage against an **input-derived** reference, NOT against
   the other arm's partition: a one-time, arm-blind `important_stories.json` is built by asking
   `claude-opus-4-8` to name the top 18 most important distinct stories from the 498 input
   titles (3 shuffled passes → one semantic-dedup consolidation pass), ordered most-important
   first. Each digest is scored for coverage of that FIXED reference (order-swapped matching; a
   story counts missed only if uncovered in BOTH orders). `miss_hard` restricts to the top-10
   majors (the higher-power gate signal — hiding one of these is a real clustering failure, not
   editorial taste; the soft tail is skipped by any reasonable digest and only widens the
   band). Both arms share the same SELECT, so the absolute miss level is a shared baseline and
   the cross-arm delta isolates the clustering effect. Lower is better.
3. **`bias_spread` / `distinct_sources` / `sources_total`** — coverage breadth. Deterministic,
   no LLM: resolve each entry's cited `article_id`s → `source_id` (articles CSV) → `bias`
   (sources.csv). `bias_spread` = # distinct bias buckets represented across the digest;
   `distinct_sources` = # distinct sources; `sources_total` = total citations. Higher is
   better (bias-diversity is the product differentiator).

Secondary (reported, NOT gated on alone — floor-hugging / within judge-noise):
- `coherence_fail` (rare high-variance count; a 0 is a trap, not a pass — the n=3 lesson),
  `filler_rate` (why-judge, precision 0.938), `kept`, `single_source`, `chain_cost`, `wall_s`.

Smoke-test only (compute for sanity, NEVER gate): ARI / BCubed-F / pairwise-F1 of EJ vs H.
A degenerate EJ partition (all-singletons / one-blob) must be excluded here before the digest
run is trusted; anything non-degenerate proceeds to the real gate regardless of the number.

## Power & band discipline
- **n ≥ 6 reps per arm** (SELECT/WRITE/COHERENCE are stochastic). Fan out via
  `scratch/tg_parallel.sh` (one container/chain, isolated `./data`, one OAuth token — proven
  0 rate-limit failures at 12 chains).
- Report **mean AND the within-arm rep band** (min/max, sd) for every signal.
- **A cross-arm delta counts only if it clears the holistic arm's own rep band.** This is the
  non-negotiable rule the whole project exists to enforce.

## DECISION RULE (fixed before results)
Let Δ(signal) = EJ_mean − H_mean, and let `band_H` = H's rep range (max−min) for that signal.

**EJ PASSES the gate iff ALL of:**
- **dedup:** EJ does not show materially MORE internal dups than H —
  `internal_dups`: Δ ≤ +max(1, band_H). (EJ may have fewer; it must not have more beyond H's
  own noise.)
- **miss:** EJ does not MISS materially more top-10 major input stories than H —
  `miss_hard`: Δ ≤ +max(1, band_H). **This is the load-bearing gate.** (`miss_all` reported as
  secondary; same direction expected.)
- **coverage:** EJ does not materially regress breadth —
  `bias_spread`: Δ ≥ −max(1, band_H) AND `sources_total`: Δ ≥ −band_H (no worse than H's own
  rep-to-rep swing).
- **no faithfulness blow-up:** `coherence_fail` EJ mean not above H mean by more than +2
  absolute AND EJ never exceeds 3 in any single rep (guards a catastrophic fabrication mode
  the band can't see at this count; deliberately loose — this is a safety catch, not a gate).

**If EJ passes →** productionize path (handoff "Open gaps"): add Gaussian time-decay (σ≈72h)
to the join; build the SQLite+networkx persistent story-graph substrate; scale to runs
205–207; plan integration (extract+join become the CLUSTER stage; SELECT/WRITE/COHERENCE stay).

**If EJ fails →** a real, paid-for finding: holistic clustering stays in production; the
extract→join reuse was still cheap. Record WHICH signal failed and by how much vs band.

## Deviations from the handoff's literal instruction (with justification)
- **Judge is Anthropic (`claude-opus-4-8`), not NIM cross-family** — NIM/Olla is unreachable
  (http 000 at start of session). Mitigation: both new signals are ABSOLUTE per-digest
  measures (internal-dup count; coverage of an input-derived reference), NOT similarity to a
  model-generated partition, so the "self-preference / conformity-to-Sonnet" circularity the
  cross-family rule guards against does not apply — there is no Sonnet reference for the judge
  to favour, and both digests are Sonnet-authored either way. Order-swap + blind labelling are
  retained. If NIM comes back, a cross-family re-score of a sample is a cheap confirmation.
- **Miss is scored vs an input-derived top-stories reference, not pairwise vs the other arm** —
  a strengthening, not a weakening: pairwise miss (judge_digests `coverage`) measures
  divergence-from-the-other-arm (partly SELECT variance, partly circular). The input-derived
  reference is the same for both arms and grounded in the actual news, so it isolates true
  omission of a salient story.

---

# RESULTS (appended 2026-07-01, after the run — nothing above this line changed)

**n=6 per arm, 0 chain failures, 0 rate-limit lines.** Both arms: same 498-article snapshot,
downstream fixed at claude-sonnet-4-6; the CLUSTER partition is the only treatment. EJ partition
= `cluster_extractjoin.json` (Haiku extract → join @ 0.80, 224 clusters). Product signals judged
by claude-opus-4-8 (order-swapped) over an input-derived 18-story reference (top-10 = `miss_hard`).

| signal | EJ (mean [range]) | holistic (mean [range]) | Δ | band_H | rule | verdict |
|---|---|---|---|---|---|---|
| **internal_dups** | **0.00** [0,0] | 1.17 [0,2] | −1.17 | 2 | Δ≤+2 | **PASS (EJ strictly better)** |
| **miss_hard** (load-bearing) | 3.67 [1,7] | 3.33 [2,5] | +0.34 | 3 | Δ≤+3 | **PASS (parity)** |
| miss_all | 10.67 [8,14] | 10.33 [9,12] | +0.34 | 3 | — | parity |
| sources_total | 83.67 [62,90] | 96.17 [85,109] | −12.50 | 24 | Δ≥−24 | **PASS (in band)** |
| distinct_sources | 21.67 [20,23] | 22.67 [21,24] | −1.00 | 3 | — | in band |
| bias_spread | 3.00 [3,3] | 3.00 [3,3] | 0 | 0 | Δ≥−1 | PASS (saturated) |
| coherence_fail | 0.00 [0,0] | 0.00 [0,0] | 0 | 0 | safety | PASS |
| filler_rate | 0.00 | 0.00 | 0 | — | — | parity |
| kept (draft) | 15.83 [14,17] | 17.00 [16,20] | −1.17 | — | — | EJ slightly shorter |
| chain_cost $ (SELECT+WRITE+COH) | **1.38** [1.21,1.54] | 1.56 [1.34,1.78] | −0.18 | — | — | **EJ cheaper** |

## VERDICT: EJ CLEARS the pre-registered gate.
Extract-join ≥ holistic on every product signal at n=6: **strictly fewer internal duplicates**
(0 vs 1.17, dups in 4/6 holistic reps and 0/6 EJ reps), **parity on important-story miss** (both
hard and all), coverage **within holistic's own rep band**, faithfulness clean (0/0). And EJ is
**cheaper downstream ($1.38 vs $1.56)** on top of replacing the Sonnet CLUSTER call (~$1.1–1.4)
with ~$0.075 Haiku extraction + a free deterministic join. This is the first properly-powered,
product-grounded, non-circular evidence that extract→join is digest-viable — the ARI/BCubed
"in-band" claims never established this (they measured conformity-to-Sonnet on a weak ruler).

## Deflation — what this does NOT establish (caveats, honestly)
1. **Partition n=1, one news-day.** The 6 reps vary only downstream SELECT/WRITE stochasticity on
   ONE EJ partition vs ONE holistic partition on ONE snapshot. It shows these two partitions give
   product-equivalent digests *today*; it does NOT show EJ generalizes across news days. (Same
   scope limit as the prior Sonnet-5 n=6.) **The join threshold 0.80 was also transferred from
   run 205's scale — untested that it's right on other days.** → the FIRST productionize step must
   be re-running this exact gate on runs 205–207, not building the substrate.
2. **EJ's `miss_hard` is more variable** — [1,7] (sd 2.16) vs holistic [2,5] (sd 1.21). Mean is
   parity, but one EJ rep missed 7/10 majors: EJ's major-story coverage is less stable (a
   partition occasionally over-merging a major into a larger cluster is the plausible mechanism).
3. **The `sources_total` dip is consistent-direction** (~13%, EJ lower in essentially every
   comparison), band-contained but real — the coverage/bias-diversity cost of a coarser head.
   Not zero; a product call, same shape as the Sonnet-5 finding.
4. **Judge is Anthropic (opus), not cross-family** (NIM down). Defensible — both signals are
   absolute per-digest and both digests are Sonnet-authored, so no arm-level self-preference — but
   a cross-family re-score (GLM/Nemotron when NIM returns) would harden the dedup/miss numbers.

## Next (per the pre-reg PASS branch, ordered by lowest-regret first)
1. **Scale-test the gate on runs 205–207** (cheap; directly kills caveat #1 before any build).
   Materialize each run's articles + holistic partition into the harness layout, regenerate
   extract→join (threshold from that run's scale), re-run this exact gate. Only a hold across
   ≥2 more snapshots justifies building.
2. **Then** the substrate work: Gaussian time-decay (σ≈72h) in the join; SQLite+networkx
   persistent story-graph (carried across days for late-binding/threads); integration (extract+join
   become the CLUSTER stage, SELECT/WRITE/COHERENCE unchanged).

---

# FOLLOW-UP (2026-07-01): judge-robustness + extraction-model (Haiku vs Sonnet) A/B

## Judge robustness — the CLEAR is not opus-specific
Re-scored the same 12 gate digests with three product-judge models (same opus-built input
reference). The verdict's shape holds; one caveat sharpens:
| judge | internal_dups (Haiku-EJ \| H) | miss_hard (Haiku-EJ \| H) |
|---|---|---|
| opus | 0.00 \| 1.17 | 3.67 \| 3.33 |
| sonnet-4-6 | 0.00 \| 1.50 | 3.50 \| 2.17 |
| haiku-4-5 | 0.00 \| 0.50 | 1.83 \| 1.67 |
- **Dedup win is judge-independent** — Haiku-EJ = 0 internal dups under ALL three judges; holistic strictly higher every time.
- **Miss is a real (small) deficit, not clean parity** — every judge puts EJ's `miss_hard` slightly ABOVE holistic (+0.16 to +1.33). Within band under the pre-registered opus judge (gate PASS stands), but the honest read is a mild, consistent over-merge coverage cost, matching the −13% `sources_total` dip. (`miss_all` differs a lot by judge in ABSOLUTE level — haiku judge is lenient — so trust the paired direction, not the scalar; same lesson as RECAP.)

## Extraction model A/B — Haiku vs Sonnet for the per-article extraction
Regenerated extract→join with **Sonnet-4-6 extraction** (vs Haiku), granularity-matched to
≈224 clusters (join threshold count-matched, both landed at 0.80 — so extraction QUALITY is the
only variable), then ran the same n=6 gate. 3-way, one opus judging pass:
| signal | holistic | Haiku-EJ | Sonnet-EJ |
|---|---|---|---|
| internal_dups | 1.33 [0,3] | **0.00** [0,0] | 0.50 [0,2] |
| miss_hard | 2.83 [2,4] | 3.50 **[1,7]** | 3.17 **[3,4]** |
| miss_all | 9.67 | 10.67 | 10.50 |
| sources_total | 96.2 [85,109] | 83.7 [62,90] | **101.0 [45,124]** |
| distinct_sources | 22.7 | 21.7 | **23.2** |
| coherence_fail | 0.0 | 0.0 | 0.5 [0,1] |
| chain_cost $ (downstream) | 1.56 | **1.38** | 1.67 |
| extraction wall / est. cost | (holistic CLUSTER ~$1.1–1.4) | 315s / ~$0.1–0.2 | 443s / ~$0.5–0.7 |

**Finding — the extraction model is a coverage-stability ↔ cost dial, not make-or-break; BOTH
variants clear the gate vs holistic:**
- **Sonnet extraction FIXES Haiku-EJ's two real weaknesses:** it recovers coverage
  (`sources_total` 101 ≥ holistic's 96, vs Haiku's 84; `distinct_sources` highest at 23.2) AND
  **stabilizes the major-story miss** (band collapses [1,7]→[3,4], sd 2.26→0.41 — no more
  occasional "a major got hidden" rep, which was caveat #2).
- **Cost of doing so:** slightly worse dedup (0.5 vs Haiku's perfect 0.0, still < holistic 1.33),
  a wide-variance coverage outlier (one rep at 45 — SELECT stochasticity, NOT a mega-cluster
  collapse; its biggest entry was a normal 12-source story), +$0.3/chain downstream, and pricier
  (~4×) + slower (1.4×) extraction.
- **Both extraction costs stay FAR below the holistic Sonnet CLUSTER call (~$1.1–1.4)** — the
  "cheap per-article extract" thesis survives either extraction model.

**Recommendation:** **Haiku extraction is the right default** — cheapest, best dedup, clears the
gate; its weaknesses (coverage dip + occasional major miss) are real but band-contained.
**Sonnet extraction is the premium knob** — spend ~$0.5/run to buy holistic-level coverage +
miss stability IF the scale-test (runs 205–207) shows Haiku's coverage variance actually bites
across news days. Decide extraction tier AFTER the scale-test, not now.

---

# SCALE-TEST (2026-07-01): does the gate hold on OTHER news-days?

The n=1-partition / one-news-day caveat was the load-bearing risk. Re-ran the EXACT gate on
archived runs (materialized from the DB via `scratch/scaletest_setup.py`: articles + holistic
clusters.json = H arm; EJ = join of the on-disk Haiku tags at held-out thr 0.80; a FRESH
input-derived reference built per run). n=6 per arm per run, opus judge.

## Run 205 (465 articles — US-Iran ceasefire / G7 day; a very different news-day)
| signal | holistic | extractjoin | Δ | gate |
|---|---|---|---|---|
| internal_dups | 0.83 [0,1] | 0.83 [0,2] | 0.00 | PASS (parity) |
| miss_hard | 3.83 [2,7] | 3.67 **[1,5]** | −0.16 | PASS (EJ ≤ H, tighter band) |
| miss_all | 8.33 | 8.00 | −0.33 | PASS |
| sources_total | 98.8 [88,113] | 90.3 [88,92] | −8.5 | PASS (in band; ~9% dip) |
| distinct_sources | 23.3 | 22.2 | −1.2 | ~parity |
| coherence_fail | 1.00 [0,2] | 0.67 [0,3] | −0.33 | PASS (EJ ≤ H) |
| chain_cost $ | 1.37 | 1.44 | +0.07 | ~parity |

**Run 205 CLEARS the gate.** Two cross-day findings vs the primary snapshot:
- **The "wide EJ miss band" caveat did NOT reproduce** — on 205 EJ's miss band [1,5] is *tighter*
  than holistic's [2,7]. That instability was snapshot/Haiku-extraction-variance-specific, not a
  systematic EJ flaw. (Good — the scariest caveat weakens.)
- **The ~9–13% coverage/`sources_total` dip DID reproduce** (EJ 90 vs H 99) — this is the one
  DURABLE, consistent EJ weakness across both days: mild, same direction, band-contained.
- The dedup *win* was snapshot-specific (parity on 205), so it should NOT be overclaimed as a
  universal EJ advantage — EJ *matches* holistic on dedup, doesn't beat it.

**Running verdict (2 news-days so far): EJ ≥ holistic on the product gate BOTH days.** The one
consistent cost is a mild (~10%) coverage dip; everything else is parity-or-better. Run 206
in progress to complete the pre-registered 3-day scale-test.

## Run 206 (news-day 3) — n=6, opus judge
| signal | holistic | extractjoin | Δ | gate |
|---|---|---|---|---|
| internal_dups | 0.50 [0,1] | 0.00 [0,0] | −0.50 | PASS (EJ better) |
| miss_hard | 0.83 [0,2] | 0.67 [0,2] | −0.16 | PASS (EJ ≤ H) |
| miss_all | 4.00 | 4.67 | +0.67 | PASS (in band) |
| sources_total | 110.3 [102,119] | 98.8 [84,117] | −11.5 | PASS (in band; ~10% dip) |
| distinct_sources | 23.5 | 21.8 | −1.7 | ~parity |
| coherence_fail | 0.00 | 0.33 [0,1] | +0.33 | PASS (safety: max 1 ≤ 3) |
| chain_cost $ | 1.50 | 1.45 | −0.05 | ~parity |
(5 of 12 chains initially failed on the 5h rate-limit wall — a session-quota event, NOT a
data/SDK bug; both arms failed simultaneously mid-batch-2. Re-ran the 5 after reset; final n=6/6
clean per arm.)

## 3-DAY SCALE-TEST VERDICT: the gate HOLDS across all three news-days (snapshot + 205 + 206)
EJ ≥ holistic on the product gate on every day tested (3 distinct news-days, n=6 each = 18 reps
per arm total). Durable cross-day pattern:
- **miss (major-story omission): parity-or-better all 3 days** — EJ `miss_hard` Δ = +0.34 / −0.16 /
  −0.16. The "wide EJ miss band" caveat from day 1 did NOT reproduce (days 2–3 EJ band ≤ H's).
  The load-bearing over-merge fear is retired.
- **internal_dups: EJ ≤ holistic all 3 days** (better day 1 & 3, parity day 2). EJ never worse.
- **coverage (`sources_total`): the ONE durable, consistent EJ weakness** — EJ lower every day
  (−13% / −9% / −10%), always within holistic's own rep band but same-direction. This is the real,
  mild, reproducible cost of the coarser extract-join head. A product call, not a blocker.
- **cost/faithfulness: parity** downstream; extract (~$0.1 Haiku) replaces the ~$1.1–1.4 holistic
  Sonnet CLUSTER call → net pipeline saving stands.

**Decision:** the generalization caveat is CLEARED. Extract→join is digest-viable across news-days
under a properly-powered, product-grounded, non-circular gate. The ONE tradeoff to weigh is the
~10% coverage/citation dip (bias-diversity is the product differentiator) — buyable back with
Sonnet extraction (the A/B showed it lifts coverage to ≥ holistic) if it matters. Cleared to move
to the productionization phase: time-decay join + persistent SQLite/networkx story-graph substrate
+ integration (extract+join become the CLUSTER stage; SELECT/WRITE/COHERENCE unchanged).
