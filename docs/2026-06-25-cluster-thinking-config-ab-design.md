# CLUSTER thinking-config A/B — experiment design (2026-06-25)

Scoping doc. The only surviving CLUSTER cost lever after the 2026-06-25 SOTA sweep
(embeddings cap ~0.69, local/NIM mid-LLMs cap ~0.70 — all far below the 0.975+ that
editorial clustering needs). Sonnet stays the clustering model; the question is
whether its **thinking configuration** can cut CLUSTER's output-token cost without
losing quality.

## Background / why this is the lever

- CLUSTER is ~38% of per-run API-equivalent cost; its cost is almost entirely
  **output** tokens (run 205: input 13 tok, output 59,900 tok).
- It currently runs with **thinking DISABLED** (`orchestrate.py` `_THINKING`). That
  decision came from a real incident: thinking-ON over ~460 articles tripped the 32k
  output-token ceiling and **looped/aborted a run**.
- With thinking off, the model has no private channel, so its reasoning lands in the
  **visible output as narration** — measured at ~80% of CLUSTER output (final
  `clusters.json` is only ~13-20% of the recorded output tokens).
- Prior failed attempt (2026-06-18): a terse "cluster internally, no narration"
  prompt on thinking-off Sonnet made it **worse** (cost +44%, ARI 0.627). The
  narration is load-bearing reasoning — you can't just suppress it.

## Hypothesis

Thinking-ON with a **capped thinking budget** + a "output only the final JSON"
prompt lets Sonnet reason in the (bounded) thinking channel and emit *only* the
compact final artifact as visible output — plausibly less **total** output
(thinking + visible) than today's uncapped thinking-off narration, while keeping
quality, and the cap prevents the 32k-ceiling loop that killed it before.

**This is genuinely uncertain** and that is why it needs an A/B:
- Claude bills thinking tokens AS output tokens, so moving reasoning to the thinking
  channel is not automatically cheaper — the saving (if any) comes from the *cap*
  bounding reasoning + the model not double-narrating for a reader.
- The cap might degrade clustering quality (truncated reasoning over ~460 articles).
- The 2026-06-18 terse-prompt failure shows quality is fragile to output changes.

## Variants to test

| id | thinking | budget cap | prompt | note |
|----|----------|-----------|--------|------|
| BASELINE | off | — | current cluster.md | today's prod config |
| V-CAP8 | on | 8k | current | bounded reasoning |
| V-CAP16 | on | 16k | current | more headroom vs 32k ceiling |
| V-CAP16-TERSE | on | 16k | "+ output ONLY final JSON, no narration" | reasoning private, output compact |
| V-BATCH | off | — | current, but cluster in 2 article-batches | orthogonal: smaller inputs avoid ceiling |

## Metrics

1. **Cost** = total output tokens (thinking + visible) per run, from the `run_usage`
   table / SDK `total_cost_usd`. Primary success metric: lower than BASELINE.
2. **Quality** = ARI of the variant's `clusters.json` vs a fixed Sonnet baseline,
   via offline `eval_stages.grade_cluster` (no model call) against
   `article_index.json`. Guardrail: must not drop materially below BASELINE.
3. **Reliability** = no 32k-ceiling abort / no loop across N runs (the original
   failure mode). Hard gate.

## Harness / how to run it (Sonnet can't run locally)

- **Constraint:** `claude -p` / Agent SDK can't run nested inside Claude Code
  (`CLAUDECODE=1` blocks it), and CLUSTER quality needs the real model — so this is
  **not** locally runnable. Two options:
  - **(a) Production shadow A/B:** run the variant CLUSTER config on the same day's
    real article set via `--write-only`-style replay against archived
    `run_artifacts` (runs 204-211 have `articles_*.csv` + baseline `clusters.json`),
    in Docker (subprocess, no CLAUDECODE). Score offline. No email/DB impact.
  - **(b) Live alternate-run A/B:** alternate config across real daily runs, compare
    `run_usage` output tokens + offline ARI. Slower, real-world, riskier.
- Recommend **(a)**: replay the 8 archived runs through each variant in Docker,
  measure output tokens + ARI offline. Deterministic-ish, no prod risk, 8-run n.

## Decision criteria

Adopt a variant only if, across the 8 replayed runs: (i) mean total output tokens
materially below BASELINE (target >20% to be worth the risk), (ii) mean ARI within
noise of BASELINE (no quality regression), (iii) zero ceiling-aborts/loops. Else
keep thinking-off. Update `orchestrate.py` `_THINKING` + cluster.md together (they're
a unit) and re-confirm on a live run before trusting.

## Risks / rollback

- The 32k-ceiling loop is the known catastrophic failure — every variant must be
  validated for it before any live use; keep the budget cap well under 32k.
- Quality regression on a quality-first product — the offline ARI guardrail is the
  gate; do not ship on cost alone.
- Rollback is config-only (`_THINKING` flag + prompt), trivially revertible.

## Status

The **replay harness this prereq calls for now exists**: `scratch/cluster-replay/`
(runs a chosen CLUSTER config via the SDK in the `digest-newsroom` container against
archived `run_artifacts`, scores ARI + pairwise precision/recall offline). It was
built for the **draft-injection** lever first (the now-SETTLED Arch-2 question),
results in `2026-06-25-cluster-draft-refine-ab-results.md`: neither a free-embedding
draft NOR a premium DeepSeek-V4-Pro-batched draft is a free win — quality anchors to
the draft, and a production-realistic DeepSeek draft is only ARI 0.671 (the 0.957/chunk
was a closed-subset artifact; real chunking can't co-locate story-mates without peeking
at gold). **Arch-2 is closed in all tested forms — thinking-config is now the only open
CLUSTER lever.** Surprising structural finding relevant to *this* doc's variants:
**CLUSTER cost is dominated by cache-read tokens (agentic turns re-reading the ~100k
article context), not just the visible/thinking output** — so a thinking-budget
variant should be measured on `total_cost_usd`, not output tokens alone. The
thinking-config variants (V-CAP8/16/TERSE/BATCH) here are **still untested**; the
harness supports them via its `--thinking` flag (off/8k/16k/adaptive). Deferred for
budget — pick up here next.
