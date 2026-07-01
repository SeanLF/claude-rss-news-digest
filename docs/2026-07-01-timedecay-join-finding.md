# Time-decay join — pre-registration + finding (2026-07-01)

Item 3 of the improvements loop. Hypothesis (from the handoff): adding a Gaussian time-decay
(σ≈72h) to the extract→join clustering "tightens clusters AND recovers the durable ~10%
citation-coverage dip" that is extract→join's one measured weakness vs holistic clustering.

**Pre-registered gate (fixed before results):** at MATCHED granularity (same cluster count as the
no-decay baseline, so granularity is not the confound), time-decay must put materially MORE
articles into the top-N clusters that SELECT surfaces (the coverage the digest cites) — otherwise
it cannot recover the dip. Only if the partition structure changes favourably would the expensive
task-grounded LLM gate be run; if the partition is ~unchanged at the hypothesized σ, the LLM gate
is a guaranteed null (identical partition → identical digest) and the deterministic structural
evidence is the stronger ruler.

## Mechanistic prior (stated before measuring)
The digest is a DAILY product: each run fetches ~24h of articles since the last run. Measured
temporal spans: today's snapshot 31.8h (86% within 24h), run 205 24.5h, run 206 similar. With
σ=72h, the Gaussian weight `exp(-Δt²/(2σ²))` for even the widest intra-window pair (~32h) is
~0.91, and ~0.95 for a typical 24h pair — i.e. NEARLY CONSTANT across the whole snapshot. A
kernel that is ~constant cannot re-order the pairwise distances, so after re-matching the
threshold to the same granularity the partition must be ~identical to no-decay. Time-decay's real
effect is PRECISION (separating same-entity DIFFERENT-time stories across days), which only bites
when the corpus spans multiple days — not RECALL/coverage within one day.

## Implementation
`cluster_extractjoin.join_tags` gained optional `published` + `sigma_hours` params: when both are
given it weighs tag-cosine similarity by the temporal kernel (`_time_kernel`) and clusters on the
combined distance `1 - sim*K` (precomputed, average linkage). Omitting both reproduces the exact
prod partition byte-for-byte (unit-tested). Missing publish times get a neutral weight (no
penalty). TDD: 3 unit tests (separation far-apart-in-time, no-op-when-omitted, missing-time-neutral).

## Structural result (deterministic, no LLM; `scratch/timedecay_structural.py`)
Two real news-days, tags already on disk (runs 205: 465 art / 206: 478 art), at matched
granularity (214 / 228 clusters). `Δcov` = change in articles-in-top-20 (coverage proxy);
`agree` = fraction of article pairs co-clustered identically vs no-decay.

| σ | run 205 Δcov | 205 agree | run 206 Δcov | 206 agree | reading |
|---|---|---|---|---|---|
| **72h** (hypothesis) | **+1** | **0.9998** | **−1** | **0.9986** | INERT — partition ~identical |
| 48h | +2 | 0.9994 | — | — | inert |
| 24h | +1 | 0.9993 | −6 | 0.9977 | ~inert |
| 12h | −1 | 0.9909 | −15 | 0.9958 | starts to churn; coverage DOWN |
| 6h | **−10** | 0.9896 | **−21** | 0.9932 | fragments day-spanning stories; coverage DOWN |

## VERDICT: hypothesis FALSIFIED. Do NOT ship time-decay.
- **At the hypothesized σ=72h, time-decay is inert** — the partition is 99.9% pair-identical to
  no-decay on both news-days, and the coverage proxy moves by ±1–2 articles (noise). It **cannot
  recover the ~10% coverage dip** because the temporal signal is near-constant within the daily
  fetch window the digest actually operates over.
- **Tightening σ does not help — it HURTS.** σ≤12h changes the partition, but in the WRONG
  direction: it fragments same-story articles published >6–12h apart (a normal news day), dropping
  10–21 articles out of the top-20 clusters. There is no σ that recovers coverage.
- The LLM product gate was **deliberately not run**: at σ=72h the partition is unchanged, so the
  downstream digest is provably unchanged — a 6-rep product gate would spend ~$13 to confirm a
  deterministic identity. The structural evidence is the stronger, cheaper ruler here.

## What DOES recover the coverage dip (already proven, not re-litigated)
The ~10% dip is a head-COARSENESS issue (extract→join's big clusters capture fewer articles than
holistic), not a temporal one. The graph-gate extraction A/B (`2026-07-01-graph-gate-
preregistration.md`) already showed **Sonnet extraction lifts `sources_total` to ≥ holistic**
(101 vs 96, vs Haiku's 84) at ~+$0.4/run — the actual coverage lever. That knob exists today
(`config.CLUSTER_EXTRACT_MODEL`); the prod default is already `claude-sonnet-4-6` (coverage-neutral).

## Disposition of the code
The time-decay path is kept **dormant and opt-in** (params default to off → prod partition
byte-identical, confirmed by test), NOT wired into `config`/`orchestrate` (no config change). Kept
rather than reverted because (a) it keeps this experiment reproducible, and (b) it is the natural
home for temporal weighting IF the planned **persistent multi-day story-graph substrate** lands
(memory `project_sonnet5_eval`: "the actual missing piece") — there the corpus spans days and the
kernel would finally bite (for precision, separating recurring-actor events across days). Trigger
to activate: a multi-day/rolling clustering window. Until then it stays off. (Reverting was
considered; kept for reproducibility + the low liability of a defaulted, tested, unwired param.)
