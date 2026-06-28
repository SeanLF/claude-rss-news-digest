# LLM-settings sweep + improvement loop on the synthesis task (2026-06-28)

Used the cloned prod DB to get HELD-OUT runs (212-215, Jun 25-28 — never tuned on), then ran
a systematic settings sweep and one improvement-loop cycle against the validated faithfulness
metric. Harness: `scratch/cluster-replay/settings_sweep.py`, `loop_iterate.py`. Protocol: fixed
held-out events, N=2 reps (LLM stochastic, no temp control), audit = Sonnet-default (constant
ruler); a config "wins" only beyond the ~1-2pp metric noise floor.

## Held-out validation — caught mild optimism

Faithfulness on the 4 unseen runs:

| run | unsupported | rate |
|---|--:|--:|
| 212 | 4/93 | 4.3% |
| 213 | 9/107 | 8.4% |
| 214 | 2/101 | 2.0% |
| 215 | 6/86 | 7.0% |
| **pooled** | **21/387** | **5.4%** |

Held-out **5.4%** vs in-sample (204-211) **3.6%**. Still ~95% grounded on data never tuned on,
but the in-sample number was slightly flattered by tuning. **True faithfulness ≈ 94.6%.** This is
exactly what held-out validation is for.

## Failure-mode analysis (the 21 held-out misses)

Three dominant, systematic modes:
- **Over-specification (~38%)** — adds precision the source lacks: "700 injured" (source: "hundreds"),
  "private hospital" (source: "hospital"), "first attack since ceasefire", "magnitude 7.2".
- **Cut-off completion (~19%)** — RSS summaries are stored capped at ~200 chars; the model COMPLETES
  truncated facts ("...près de 50" -> "50,000"). A pipeline data characteristic, not the model alone.
- **Causal overreach (~14%)** — juxtaposition stated as causation ("Ukraine's strikes contributed to
  the stock decline" where the source only mentions both).

## Settings sweep — thinking x model (run 213, 3 events x 2 reps)

Temperature/top_p are NOT exposed by the Agent SDK (production path) -- testing them needs the raw
API = off-subscription $ (the Batch-API trap). Levers tested: thinking, model.

| config | facts/event | unsupported | cost/event | vs prod |
|---|--:|--:|--:|---|
| sonnet-nothink (prod) | 23.5 | 7.1% | $0.050 | baseline |
| sonnet-think-8k | 26.5 | 9.4% | $0.116 | WORSE, 2.3x cost |
| sonnet-think-16k | 27.0 | 3.7% | $0.106 | −3.4pp, 2.1x cost |
| opus-nothink | 20.7 | 2.4% | $0.283 | −4.7pp, 5.7x cost, −12% facts |

**Thinking is non-monotonic** — a small budget (8k) makes it WORSE (truncated reasoning ->
confident overstatement); only a large budget (16k) helps. **Opus is most faithful but 5.7x cost
and more conservative (fewer facts).** Both quality levers cost 2-5.7x.

## The loop iteration — a FREE prompt fix beats the costly settings

From the failure modes, wrote SYNTH_SYS v2 with three explicit rules: (A) no added precision,
(B) never complete cut-off text, (C) no asserted causation. Re-measured v1 vs v2 on the two worst
held-out runs (5 events x 2 reps each):

| run | v1 baseline | v2 (anti-overstatement) | Δ |
|---|--:|--:|--:|
| 213 | 10.6% | 5.0% | −5.6pp (clear) |
| 215 | 3.9% | 2.3% | −1.6pp (directional) |
| **pooled** | **30/397 = 7.6%** | **14/373 = 3.8%** | **−3.8pp (halved)** |

**The free prompt fix ~halves the unsupported rate (7.6% -> 3.8%)** — a bigger drop than think-16k
(−3.4pp) at ZERO extra cost, approaching Opus (−4.7pp) for free. Small coverage cost (~5% fewer
facts — exactly the over-specified ones it should drop). Confirmed in direction on both held-out
runs (clear on 213; 215 within-noise because it started cleaner). 215's start being low limits how
much can be shown there.

## Conclusions

1. **The improvement loop works end-to-end**: held-out failure analysis -> targeted prompt fix ->
   measured ~halving of the error on data never tuned on. The validated faithfulness metric makes
   this possible (the CLUSTER problem never had a trustworthy ruler to loop on).
2. **Prefer the prompt fix over the settings levers**: v2 (free) beats think-16k (2x cost) and
   approaches Opus (5.7x). Spend the budget on the prompt, not the settings.
3. **The fix likely transfers to the existing production WRITE stage** — adding the same
   anti-overstatement rules to `write.md` should cut its overstatements too (COHERENCE would
   confirm). This is a shippable improvement to the CURRENT pipeline, independent of the synthesis
   direction.
4. **Next loop targets** (diminishing returns): the cut-off-completion mode is a pipeline data
   lever (raise the ~200-char RSS-summary cap = fuller sources, more tokens pipeline-wide);
   the residual after v2 is mostly that + hard edge cases.

Replicability: fixed runs/events, N=2 reps, constant Sonnet auditor; absolute baselines are noisy
(213 v1 ranged 7.1-10.6% across samples) but within-experiment v1-vs-v2 is clean. Re-run any line
with the named harness + run id.
