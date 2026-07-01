# Sonnet 5 on the JUDGMENT stages — pre-registration (2026-07-01)

Item 2 of the improvements loop (`2026-07-01-improvements-loop-handoff.md`). The deferred
per-stage A/Bs: does moving a JUDGMENT stage (WRITE first, then SELECT) from Sonnet 4.6 to
Sonnet 5 improve the digest enough to justify the cost? **Arms, signals, and the decision
rule are fixed here BEFORE any result is looked at** — the discipline this project keeps
relearning (`feedback_eval_ill_posed_metric`). Results are appended below the line.

## Why absolute signals only (no pairwise preference judge)
Unlike the clustering gate (both digests were 4.6-authored, so an Anthropic judge could not
show arm-level self-preference), here ONE arm is Sonnet-5-authored. A pairwise "which draft is
better" LLM judge from the Anthropic family could exhibit family preference for the S5 arm —
and NIM/Olla (the cross-family panel) is DOWN this session (curl to 127.0.0.1:40114 fails). So
this gate uses ONLY signals that are robust to that bias:
- **filler_rate** — the validated `why_judge` golden (`eval_why_judge.judge_why`, judge pinned
  `claude-sonnet-4-6`, validated vs a 45-case HUMAN golden at agreement 0.867). It is an
  ABSOLUTE per-line binary classifier (does this `why_it_matters` add a new dimension, or is it
  filler), not a cross-arm preference — the judge scores each line on its own merits against a
  human-anchored rubric, identically for both arms.
- **coherence_fail** — headlines COHERENCE (at 4.6) marks `pass:false`. Absolute faithfulness;
  both arms judged by the identical downstream COHERENCE call.
- **coverage** — `sources_total` / `distinct_sources` / `single_source`: DETERMINISTIC (resolve
  cited `article_id`s → sources; no LLM). Coverage breadth = the bias-diversity product signal.
- cost / latency: `chain_cost`, `wall_s`.

If NIM returns, a cross-family absolute re-score of filler is a cheap confirmation (deferred).

## WRITE A/B (primary — the strongest instrumented stage)

- **Arm C (control = prod):** WRITE @ `claude-sonnet-4-6`, `thinking:disabled`, effort unset
  (exact prod config).
- **Arm S5:** WRITE @ `claude-sonnet-5`, `thinking:adaptive`, `effort:medium`. This is the
  model-aware config the prior session found to be the parity point and to KILL the S5
  rewrite pathology (`thinking:disabled` 400s on S5 and, when forced, triggers Write×3
  self-revision). Uses the `cluster_extractjoin._thinking_for` rule: never send `disabled` to
  a next-gen model. ~+80% stage cost vs 4.6 (cheap in absolute terms, ~$0.45→$0.82).

Held fixed for both arms: the partition (today's 499-article extract-join `clusters.json`),
`selected.json` (SELECT already run at 4.6 in the snapshot), COHERENCE @ 4.6, assemble. The
WRITE **model+config is the only treatment.** n = 6 reps per arm (WRITE is stochastic).

### Signals + reference band
Report mean AND the within-arm rep band (min/max, sd) for every signal. The reference band is
**arm C's own rep range** at n=6. A cross-arm delta counts only if it clears that band.

### DECISION RULE (fixed before results)
Sonnet 5 costs ~+80% on WRITE, so it must EARN the switch with a real quality gain, not parity.

**Switch WRITE → Sonnet 5 iff ALL of:**
1. **Quality gain (load-bearing):** S5 `filler_rate` is materially BETTER than C —
   `Δfiller = S5_mean − C_mean ≤ −band_C` (a real improvement that clears C's own rep noise).
   Parity (|Δ| within band) → DO NOT switch (negative result: 4.6 is the cost-efficient choice).
2. **No faithfulness regression:** `coherence_fail`: S5_mean ≤ C_mean + max(1, band_C), and S5
   never exceeds 3 in any single rep (safety catch).
3. **No coverage regression beyond noise:** `sources_total`: Δ ≥ −band_C. (Confound noted: S5
   is known to cite more precisely — fewer, claim-backing citations vs 4.6's cite-the-whole-
   cluster habit; a materially lower `sources_total` at equal quality is a real bias-diversity
   cost and counts against the switch.)

**Floor-hug caveat (pre-registered):** if BOTH arms hit `filler_rate ≈ 0` (prior n=1 was 0/17
both), the primary signal is non-discriminating — that is itself the finding (both models write
non-filler `why_it_matters`; no quality gap to pay +80% for → keep 4.6). Do NOT rescue it with a
pairwise preference judge (family bias, NIM down). Report the null honestly.

## SELECT A/B (secondary — run if WRITE done with budget/time)

- **Arm C:** SELECT @ 4.6 (prod). **Arm S5:** SELECT @ S5 (`thinking:adaptive`, `effort:medium`).
- Held fixed: partition; WRITE + COHERENCE @ 4.6; assemble. SELECT model is the only treatment.
- Signals: same product signals scored on the digest (SELECT drives tiering/coverage/miss);
  `sources_total`/`distinct_sources` (coverage), `kept`/shape, faithfulness, cost. n=6.
- Decision rule: same shape — switch only for a coverage/quality gain clearing C's band; parity
  → keep 4.6.

## Synthesis (thread-synthesis) — scope decision
Synthesis = the evolving story-thread subsystem (`THREADS_*`). Its trustworthy prior is
faithfulness ~96.4% (gold-free cross-family). A full S5 synthesis A/B needs the thread pipeline
wired into this harness (delta-from-facts, latebind) — heavier than WRITE/SELECT. **Deferred
within Item 2** unless WRITE+SELECT leave ample budget: recorded as a scoped-out decision, not
skipped silently. The faithfulness prior is the guard; wiring S5 there is a separate build.

## Prod-wiring note
Wire a stage to S5 in prod ONLY if it clears its gate. On wiring, add `claude-sonnet-5` to
`usage.py::_PINNED_MODEL_IDS` (else every run logs "model drift") and set the stage config
model-aware (`_thinking_for`; never `disabled` on S5; `effort:medium`).

---

# RESULTS (appended after the run — nothing above this line changes)

## WRITE A/B (n=6 per arm, 0 chain failures, 2026-07-01)
Partition = today's 499-article extract-join snapshot; SELECT held fixed (snapshot's
selected.json @4.6); COHERENCE @4.6; WRITE model+config is the only treatment. Harness
`scratch/sonnet5_judgment_gate.py` (fan-out `judgment_gate_parallel.sh`, agg `jg_aggregate.py`).

| signal | c46 (control) mean [range] | s5 mean [range] | Δ | rule | verdict |
|---|---|---|---|---|---|
| **filler_rate** (primary) | **0.000** [0,0] | 0.114 [0.05,0.21] | +0.114 | Δ≤−band_C(0) | **FAIL — S5 worse, clears band** |
| coherence_fail | 1.167 [1,2] | **0.000** [0,0] | −1.167 | safety ok | S5 better (faithfulness) |
| sources_total | 108.8 [107,112] | 78.2 [65,91] | −30.7 | Δ≥−band_C(−5) | **FAIL — −28%** |
| distinct_source_ids | 21 [21,21] | 21 [21,21] | 0 | — | **identical (bias diversity unchanged)** |
| bias_spread | 3 [3,3] | 3 [3,3] | 0 | Δ≥−1 | identical |
| single_source | 0 | 1 | +1 | — | S5 cites sparser |
| kept | 19.2 | 19 | −0.2 | — | parity |
| chain_cost $ | 1.08 [0.86,1.23] | 1.36 [1.27,1.45] | +0.28 | — | S5 +26% |
| wall_s | 470 | 437 | — | — | ~parity |

### VERDICT: KEEP Sonnet 4.6 for WRITE. Do NOT switch.
Sonnet 5 delivers **no quality gain** — it is WORSE on the validated `why_judge` golden
(filler 0.114 vs 0.000, clearing 4.6's own [0,0] band; ~11% of `why_it_matters` lines flagged
vs 4.6's zero), cites ~28% fewer sources, costs +26%, and hit an invalid-JSON WRITE output on
one rep (unescaped control char → caught by the prod once-retry, recovered). Its one real
advantage — 0 coherence failures vs 4.6's ~1.2 — does not offset a quality regression the
digest reader would feel. The pre-registered decision rule fails at criterion 1 (no quality
gain) regardless of the coverage nuance below.

### Deflation (what NOT to overclaim)
1. **The `sources_total` "regression" overstates product impact.** `distinct_source_ids` (21)
   and `bias_spread` (3 buckets) are IDENTICAL across arms — the bias-diversity signal that is
   the actual product differentiator is UNCHANGED. S5 simply cites more sparingly per story
   (single_source 1 vs 0), a citation-density philosophy difference, not a lost-source-diversity
   problem. So don't sell this as "S5 loses coverage"; sell it as "fewer citation links, same
   source/bias breadth." The gate still says KEEP 4.6 on criterion 1 alone.
2. **Filler judge is 4.6-pinned** (golden built on 4.6-era drafts). S5's higher filler COULD be
   partly a judge-style bias against S5's terser `why_it_matters`. But the judge is human-golden-
   validated (0.867) and absolute per-line; the honest floor is "S5 does not IMPROVE filler."
   Even discounting filler entirely, there is no measured S5 WIN to justify +26% cost → KEEP 4.6.
3. **NIM down** → no cross-family filler re-score (deferred). Both signals are absolute; deferred
   confirmation only, not load-bearing for the KEEP verdict.

## SELECT A/B (n=6 per arm, 0 chain failures, 2026-07-01)
Partition = today's snapshot; SELECT model is the only treatment; WRITE + COHERENCE @4.6 both
arms (so WRITE stochasticity is shared). Chain = SELECT → WRITE → COHERENCE → assemble.

| signal | c46 (control) mean [range] | s5 mean [range] | Δ | rule | verdict |
|---|---|---|---|---|---|
| **filler_rate** (primary) | **0.000** [0,0] | 0.000 [0,0] | 0 | Δ≤−band_C(0) | **floor-hug — non-discriminating** |
| coherence_fail | 1.667 [1,2] | 0.667 [0,1] | −1.000 | safety ok | S5 mildly better (rare-event) |
| sources_total | 95.2 [86,102] | 91.5 [83,102] | −3.7 | Δ≥−16 | PASS (parity, in band) |
| distinct_source_ids | 20 [20,20] | 19 [19,19] | −1 | — | ~parity |
| bias_spread | 3 [3,3] | 3 [3,3] | 0 | Δ≥−1 | identical |
| kept | 17.0 [16,19] | 17.3 [16,20] | +0.3 | — | parity |
| chain_cost $ | 1.70 [1.52,1.79] | 2.12 [2.00,2.20] | +0.42 | — | S5 +25% |
| wall_s | 684 | 649 | — | — | ~parity |

### VERDICT: KEEP Sonnet 4.6 for SELECT. Do NOT switch.
`filler_rate` is floor-hug (0/0 both arms — non-discriminating: the current prompt already
yields ~zero filler, so there is no headroom for a better model to show on this signal).
Coverage / bias-spread / kept are all parity; S5 costs +25%. The pre-registered gate fails at
criterion 1 (no measurable quality gain). S5 shows a mild faithfulness edge (coherence_fail 0.67
vs 1.67) — but that is the exact rare-event count the pre-reg flagged as secondary/floor-hugging
("a 0 is a trap, not a pass — the n=3 lesson"); bands overlap at 1, so it is a soft, low-count
signal, not a band-clearing win.

---

# ITEM 2 CONCLUSION — Sonnet 5 on judgment stages: KEEP 4.6 on both measured stages

**WRITE → KEEP 4.6** (S5 worse on filler, sparser citations, +26%, a JSON wrinkle).
**SELECT → KEEP 4.6** (filler floor-hug, coverage/bias parity, +25%, no quality gain).

Both properly-powered (n=6), pre-registered, product-grounded A/Bs land on the SAME negative:
**Sonnet 5 does not deliver a digest-quality win on the judgment stages worth its ~+25-45% cost
on this snapshot.** The current 4.6 WRITE prompt already produces ~zero filler per the validated
golden, so the model-quality headroom on the strongest instrumented signal is small.

**The ONE honest, consistent, positive S5 signal: fewer COHERENCE failures in BOTH gates**
(WRITE 0 vs 1.17; SELECT 0.67 vs 1.67 — S5 more headline-faithful either way). It is a mild,
low-count, rare-event edge (deliberately NOT gated on), not enough to justify the switch today —
but it is the reason to REVISIT Sonnet 5 on judgment IF (a) faithfulness becomes the priority, or
(b) S5 pricing drops (the intro $2/$10 ends 2026-08-31 → then $3/$15, so cost only rises). Not a
"never," a "not now, and here's the trigger to reopen."

`usage.py::_PINNED_MODEL_IDS` deliberately NOT changed — no S5 stage is wired to prod (the drift
warning correctly fired for the S5 eval arms, exactly as designed).

## Synthesis (thread-synthesis) — SCOPED OUT of this pass (reasoned, not skipped)
Deferred with cause: (1) it needs the THREADS pipeline (delta-from-facts, latebind) wired into the
A/B harness — materially heavier than the WRITE/SELECT stage-swap, which reused existing
orchestration; (2) its trustworthy prior is faithfulness ~96.4% (gold-free cross-family) — a guard
already in place; (3) BOTH cheaper judgment stages returned the SAME negative, lowering the prior
that synthesis alone would flip positive; (4) model latency ~4× today made each 12-container batch
~15-20 min, and three loop items remain. Expected value of a third large S5 judgment eval is low.
Recorded as a scope decision. If reopened (per the faithfulness/pricing triggers above), wire S5
into the thread-synthesis call behind the same model-aware `_thinking_for` config and gate on the
gold-free faithfulness audit, not filler.

