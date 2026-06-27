# Evaluating clustering with NO ground truth — literature + protocol (2026-06-26)

**Handoff doc for tomorrow's session.** Companion to `2026-06-26-cluster-eval-methodology.md`
(which established empirically that ARI-vs-one-Sonnet-gold is a weak ruler). This doc records
what the *literature* says about the right way to evaluate an ill-posed task when the only
"reference" is itself a stochastic model — and a concrete protocol that falls out of it.

Source: a deep-research sweep (6 strands, 25 sources, 25 claims verified 3-0 adversarially,
0 killed). Raw report: workflow `wf_8139edc4-b12` output (transcript dir under this session).
**Cost note:** that sweep burned ~24M tokens / ~110 agents — overkill for a literature pull.
Next time use 1-2 targeted `WebSearch` or 2-3 plain research agents (see memory
`feedback_deep_research_token_cost`).

## TL;DR
- The core instinct is **literature-validated**: one stochastic Sonnet run is NOT gold;
  "no-single-ground-truth evaluation" is the correct, established framing.
- The cleanest fix I proposed from memory — **Dawid–Skene "infer latent truth + per-method
  competence"** — is **formally broken for our case** (correlated runs of one model violate the
  conditional-independence that D–S identification needs → latent prevalence unidentified, set
  = [0,1]). Don't build on it without external (human) calibration.
- The **actionable upgrade**: score the cheap method against a *distribution* of Sonnet runs
  with a **soft metric (Wasserstein/Manhattan)**, reported **relative to the Sonnet-vs-Sonnet
  self-agreement band**, not a fixed bar. Anchor with a **small human-adjudicated subset**.
- **Stability ≠ validity** — Sonnet's 0.60–0.88 self-consistency is an ambiguity band, NOT a
  quality/correctness proxy (all-in-one-cluster is maximally stable).
- The **recall hole** Sean identified ("what should've been shown but wasn't", no observable
  negatives) remains the **least-resolved** strand — only pointers (IR pooling / infAP), no
  verified method yet.

---

## Strand 1 — perspectivist NLP / learning-from-disagreement — SOUND ✓ (verified)
Disagreement on ill-posed/subjective tasks is legitimate variation to preserve, not noise to
collapse into one gold. **This is the most directly actionable strand.**

- **Best surveys:** Uma et al., *"Learning from Disagreement: A Survey,"* JAIR 2021
  (jair.org/.../12752); Frenda et al. 2024 (link.springer.com/article/10.1007/s10579-024-09766-4);
  Cabitza, Campagner & Basile 2023 (AAAI) — formalized perspectivism.
- **The metric guidance (cite the 2025 edition):** the LeWiDi shared tasks score against the
  *distribution* of disagreeing references with **soft metrics**. Trajectory: hard-F1 → soft
  **cross-entropy** (LeWiDi-2023, arXiv 2304.14803) → **Manhattan + Wasserstein/EMD distance**
  (LeWiDi-2025, arXiv 2510.08460) after Rizzi et al. 2024 showed cross-entropy misbehaves.
  LeWiDi-2025 splits eval into *soft-label* (predict the population distribution) and
  *perspectivist* (predict each annotator).
- **Why it matters to us:** Gordon et al. (CHI 2021, disagreement deconvolution) showed
  single-aggregated-truth metrics **systematically overstate** performance (oracle .95→.73 once
  disagreement is modeled). Scoring a cheap clusterer vs one aggregated reference can flatter
  *or* mis-rank it.
- **CAVEAT (firm):** perspectivism was built for *human* interpretive variation. One model's
  sampling stochasticity is not the same epistemic object — transfer is an **analogy**, and
  LeWiDi scores item-label disagreement, not partitions, so it needs reformulating to a
  **per-pair co-membership distribution** across runs. Not a turnkey clustering metric.

## Strand 2 — Dawid–Skene truth-inference — shape maps, RESCUE BROKEN ✗ (verified)
- **Shape is right:** D–S (1979, EM) and successors jointly infer latent label + each
  annotator's confusion matrix from noisy answers with no gold. A 2025 paper even casts
  clustering algorithms *as* D–S experts (arXiv 2509.25395).
- **But it does NOT save us:** identification **requires conditional independence of annotators
  given the truth**. Correlated runs of one model (Sonnet sampling) or feature-sharing methods
  violate it → unrestricted two-component mixture → **latent prevalence completely unidentified,
  identified set = [0,1]**; weak competence restrictions don't fix it.
  → Chen/Rambachan/Tamer, *"Partial Identification from LLM Prompts,"* arXiv 2606.15031 (directly on point).
- **Empirics:** Zheng et al., *"Truth Inference in Crowdsourcing: Is the Problem Solved?"*
  PVLDB 2017 — 17 algorithms / 5 datasets, "no method dominates… not solved."
- **Takeaway:** can't reconstruct a trustworthy reference from multiple Sonnet runs **without
  external calibration** (a small human-adjudicated anchor).

## Strand 5 — stability / internal validation — real signal, but STABILITY ≠ VALIDITY ✓ (verified)
- **Real ground-truth-free signal:** clustering quality as reproducibility under
  resampling/perturbation. Reviews: Liu 2022, *"Stability estimation for unsupervised
  clustering: A review,"* WIREs (PMC9787023); Monti et al. consensus clustering; Lange et al.;
  Ben-Hur. Recent index: CSAI (scitepress 2025/133091).
- **Critical caution aimed right at us:** *stability is not validity.* All-in-one-cluster is
  always maximally stable; stable clusterings can be wrong; true structure can be unstable; and
  there is no unique true clustering even when "true" labels exist.
  → Ullmann, Hennig & Boulesteix 2022 (WIREs DMKD, widm.1444 / arXiv 2103.01281); von Luxburg
  *"Clustering Stability: An Overview"*; Ben-David & von Luxburg *"A Sober Look at Clustering Stability."*
- **Consequence:** Sonnet's 0.60–0.88 self-consistency is an **ambiguity band**, NOT a quality
  score. A cheap method could be *more* stable AND worse.

---

## Strands 3, 4, 6 — SEARCH-SURFACED ONLY (NOT adversarially verified — verify tomorrow)
These produced no *verified* claims (the verify phase was budget-capped at 25/114 claims), but
the search agents surfaced real surveys/papers. **Treat as leads, not confirmed.** Sean OK'd
using them *if they're genuine lit reviews* — flagged below by type.

**Strand 6 — LLM-as-judge meta-evaluation / reference-free eval** (most relevant to us):
- **SURVEY:** Gu et al., *"A Survey on LLM-as-a-Judge,"* arXiv 2411.15594 (frequently updated) —
  central question "how to build reliable LLM-as-a-Judge," catalogs bias mitigations.
- Position-bias (verdict flips 17–40%): arXiv 2406.07791 (we already use order-swap in
  `adjudicate.py` because of this).
- Others surfaced: arXiv 2411.16594, arXiv 2410.21819.
- **Why it matters:** our `judge_digests.py` is a *single forward-pass* judge — no order-swap,
  no tie option. This strand is where to find the recommended protocol to harden it.

**Strand 4 — IR pooling / incomplete judgments / PU learning** (the recall hole — HARDEST, least covered):
- **Canonical method paper:** Büttcher, Clarke, Soboroff & Cormack, *"Reliable Information
  Retrieval Evaluation with Incomplete and Biased Judgements,"* SIGIR 2007.
- **infAP (inferred AP under incomplete judgments):** Yilmaz & Aslam (nist.gov/.../inferredAP.pdf).
- Also: arXiv 2206.02423.
- **The idea for our recall problem:** can't enumerate "all stories that should've been shown,"
  so **pool the union of what every method surfaces, human-judge the pool, treat unjudged as
  negative** — TREC's 30-year answer to unobservable negatives. *No verified method yet — this
  is the open research task.*

**Strand 3 — co-training / consensus / pseudo-labeling + active learning:**
- **SURVEY:** *"From Selection to Generation: A Survey of LLM-based Active Learning,"* arXiv
  2502.11767 (2025) — formalizes LLMs as a committee (Query-by-Committee), variability across
  generations as the uncertainty signal → route disagreement region to human spot-checks.
- Also: Wiley int/6472544 (primary); Lilian Weng active-learning blog (background).
- **The idea:** where Sonnet runs *agree* = pseudo-truth we can score against; where they
  *disagree* = the region to spend scarce human labels (active learning). We stumbled into this
  with the AMBIG control; this strand is the principled version.

---

## The synthesized protocol (what to build/validate)
Minimal valid no-gold score for the news-clustering case, combining strands 1+2+5:
1. **N≥10 Sonnet runs → per-pair co-membership distribution** (the soft reference). We already
   have 7 refs for run 204, 3 for 205 in `scratch/cluster-replay/out/refs/`.
2. **Score the cheap method by Wasserstein/Manhattan distance to that distribution** — not hard
   ARI/BCubed vs one run.
3. **Report relative to the Sonnet-vs-Sonnet self-agreement band**, never a fixed 0.75 bar.
4. **Escape the [0,1] non-identification with a small human-adjudicated subset** (Sean as
   oracle on the disagreement pool — tiny n because only boundary/published pairs count).
   External calibration is the ONLY anchor; no aggregation substitutes.

## Open problems (for tomorrow / future)
1. **RECALL with no observable negatives** (strand 4) — the genuinely unsolved one. Dig the IR
   pooling / infAP literature; design the pooled-union + human-judge measure.
2. **Harden the digest judge** (strand 6) — add order-swap + tie option to `judge_digests.py`
   per the LLM-as-judge survey before trusting its missed/novel counts.
3. **Reformulate LeWiDi soft-distance to partitions** (strand 1) — per-pair co-membership
   distribution + Wasserstein; is it tractable/meaningful on our singleton-heavy data?
4. **Does the "validate then park" decision still hold?** The empirical work (methodology doc)
   said cheap clustering is shippable; this lit doesn't overturn that — it sharpens *how to
   measure it honestly* if/when we revisit.

## START HERE tomorrow
- Re-read this + `2026-06-26-cluster-eval-methodology.md`.
- Decision point: (a) implement the soft-distance protocol + hardened judge to get the *valid*
  ship/park number, or (b) accept the empirical "shippable" verdict and move to engineering.
- If researching the recall strand: **1-2 targeted searches**, NOT the deep-research harness.
- Harness lives in `scratch/cluster-replay/` (band_eval, adjudicate, join_*, run_downstream,
  judge_digests, refine_borderline). Refs in `out/refs/`.
