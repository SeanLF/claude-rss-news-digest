# Synthesis direction — session handoff / state + next steps (2026-06-28)

Consolidated handoff for the multi-day synthesis-direction investigation (so a fresh session
can resume after compaction). Detailed findings are in the dated docs below; this is the map +
the decisions + what to do next. All work committed; research harness in gitignored
`scratch/cluster-replay/`.

## The big idea (validated)

**Move Sonnet from clustering to synthesis.** Instead of cluster→pick-representative→summarize,
treat each digest story as a comprehensive Sonnet SYNTHESIS across the event's full coverage.
Grew out of the CLUSTER cost work but is a QUALITY play. Validated thoroughly (rigor + held-out).

## Committed docs (read in this order)

1. `2026-06-27-graph-synthesis-direction.md` — core validation: faithfulness, coverage, cost,
   robustness, the method-validation (anti-circularity) pass, generalization to n=4, cheap-bundle n=2.
2. `2026-06-28-synthesis-forward-ideas-pocs.md` — forward ideas: late-binding (winner), temporal
   threading, self-repair (dead); and the UNIFIED evolving story-thread (the "even better" change).
3. `2026-06-28-settings-and-loop-experiment.md` — held-out validation on fresh runs, LLM-settings
   sweep, and the improvement-loop WIN (the v2 anti-overstatement prompt fix).
4. `2026-06-26-cluster-eval-methodology.md` + `2026-06-26-news-clustering-prior-art.md` — the
   CLUSTER cost work that preceded this (cheap clustering is shippable; ship/park is a product call).

## Validated findings (the numbers that matter)

- **Faithfulness: ~94.6% held-out** (5.4% unsupported on runs 212-215 never tuned on; in-sample
  3.6% was mildly optimistic). The faithfulness AUDIT is the key asset: gold-free, instrument-
  validated (injection 6/6 both families), cross-family confirmed (no self-preference). It is the
  trustworthy ruler that makes an improvement loop possible.
- **Coverage**: synthesis covers ~80% of available facts vs the current digest item's ~48%, and
  +16-20pp even at EQUAL length (not just "longer"). Measured vs an independent Nemotron reference.
- **Cost**: per-event synth ~= cost-neutral vs dropping Sonnet CLUSTER; BATCHED synthesis −57% cost
  but −25% facts (skim) — a tunable frontier.
- **Robustness**: the `coherent_event=false` safety valve fires on incoherent bundles (3× in the
  wild) and refuses to fabricate; cheap-bundle end-to-end holds (n=2, 3.7% on the hard run).
- **Settings**: Agent SDK exposes thinking+model, NOT temperature (temp = off-subscription, dead).
  Thinking non-monotonic (8k worse, 16k better @2× cost); Opus most faithful @5.7× cost.

## SHIPPABLE WINS (do these)

1. **v2 anti-overstatement prompt rules** — held-out failure analysis found 3 modes
   (over-specification, cut-off completion from the ~200-char RSS-summary cap, causal overreach);
   a FREE prompt fix (rules A/B/C in `loop_iterate.py SYNTH_SYS_V2`) HALVES the unsupported rate
   (7.6%→3.8% pooled across 2 held-out runs), beating think-16k (2×) and approaching Opus (5.7×) for
   free. **The production WRITE stage has the SAME overstatement modes** (write_audit.py: France-vs-
   Paris, "toppled", "Thursday", fabricated attributions). PORT the rules into `.claude/agents/write.md`
   (no added precision / no completing cut-off text / no unsupported attribution / no asserted
   causation) — a shippable improvement to the LIVE digest. Validate via run_downstream A/B + write_audit
   before shipping. [IN PROGRESS: corrected production baseline measuring; first cut 94%/5-per-item
   was inflated by auditing why_it_matters — the real summary-only rate is lower, being measured.]
2. **The evolving story-thread** (the "even better" change) — a persistent story-graph across days
   (late-binding subgraph + threading delta + open-question ledger). Demonstrated on a 4-day Iran
   thread: 13 questions raised→resolved, categorically better than daily snapshots. Faithfulness was
   the catch (22.5% first cut → 8.4% after walling MEMORY from FACTS; residual via audit-drop). This
   is the genuinely-novel direction; build it as a real feature (event-level edges, persisted thread
   state on `shown_narratives`+`weekly_recap`, audit-drop wired in).

## Honest verdict on the work

Good engineering + research-grade evaluation rigor, NOT novel research (confirms known MDS/graph/
eval literature) — EXCEPT the evolving-thread, which is the closest to a genuine contribution.

## Next steps (prioritized)

1. **Finish + ship the v2 WRITE fix**: confirm the corrected production overstatement baseline, run
   the write.md A/B (v1 vs v2 via run_downstream on a held-out run), audit both, ship if it improves
   without cratering coverage. Likely the highest-value, lowest-risk real improvement.
2. **The 200-char RSS-summary cap** is the next loop target (a pipeline data lever): raising it would
   kill the cut-off-completion failure mode but costs more tokens pipeline-wide (CLUSTER/SELECT/WRITE
   all use these summaries). Measure cost/benefit.
3. **Build the evolving story-thread** as a flagged feature (the "even better" change).
4. Late-binding edges: entity-bag (80% pure) + the synthesis guardrail is sufficient; embeddings are
   NOT cleaner (70%); a hybrid (entity AND embedding) is the only untested cleaner-edge idea.

## Harness inventory (`scratch/cluster-replay/`, gitignored)

- Synthesis + faithfulness: `synth_experiment.py` (synth+audit@scale), `synth_batched.py` (batched
  cost/quality), `synth_poc.py` (single-event compare).
- Audit validation: `audit_validate.py` (ground-truth injection), `audit_crossfamily.py` (self-
  preference check), `coverage_eval.py` (coverage vs independent reference, incl. tightened/length).
- Forward ideas: `late_bind.py` (soft-graph synthesis), `temporal_thread.py` (delta), `self_repair.py`
  (dead), `evolving_thread.py` (the unified thread), `embed_neighborhood.py` (edge-quality).
- Settings + loop: `settings_sweep.py` (thinking×model), `loop_iterate.py` (v1 vs v2 fix).
- Production: `write_audit.py` (production WRITE overstatement), `run_downstream.py` (SELECT→WRITE),
  `join_materialize.py` (regenerate cheap clusters), `extract_tags.py` (Haiku tags).
- DB: working `data/digest.db` is now the cloned prod (runs 1-215); pre-clone backup `data/digest-211.db`.

## Replicability

Every result re-runs with the named script + run id. Protocol: FIXED held-out runs (212-215 are
the unseen test set; 204-211 were tuned on), N=2 reps (LLM stochastic, no temp control), constant
Sonnet-default auditor. Absolute baselines are noisy (~1-2pp); within-experiment v1-vs-v2 is clean.
A change "wins" only beyond the ~2pp noise floor.
