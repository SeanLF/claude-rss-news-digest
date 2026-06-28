# The graph-synthesis direction: move Sonnet from clustering to synthesis (2026-06-27)

A direction that came out of the CLUSTER cost investigation but is really a **quality** play.
Premise (Sean's): stop treating clustering as a cost to minimize; treat news as a **graph**
(events/entities/time, edges = same-event / shared-entity / reaction / follow-up) and present
each digest "story" as a **comprehensive synthesis across a node/edge/subgraph** — let Sonnet do
what only it does well (read all the coverage, find what matters, tie it together), instead of
spending ~38% of the run partitioning articles into buckets.

The reframe: **cheap extraction builds the graph; Sonnet's effort goes into synthesis.** We
already proved (see `2026-06-26-cluster-eval-methodology.md`) that cheap grouping (Haiku entity
extract → join) holds up at the digest level. So redirect the freed Sonnet budget from grouping
(cheap methods tie it) to synthesis (only Sonnet is good at it).

## What the literature says (verified — arXiv IDs checked against arxiv.org)

Multi-document summarization (MDS) of news with LLMs is a **yellow light**, not green:
- **DiverseSumm / "Embrace Divergence"** (Huang et al., Salesforce, [2309.09369](https://arxiv.org/abs/2309.09369), Sep 2023): strong LLMs stay ~95% faithful but cover only **~37% of the distinct facts** across sources — they SKIM and anchor on first/last articles. → guardrail: **force coverage, list distinct facts**.
- **"From Single to Multi"** (Belem et al., Megagon, [2410.13961](https://arxiv.org/abs/2410.13961), Oct 2024): the dominant MDS failure is merging non-shared info / overgeneralization (not wholesale invention). → guardrail: **cite-or-omit, no invented bridges**.
- **"Faithful Summarisation under Disagreement"** (Aghaebe/Moosavi et al., Sheffield, [2601.04889](https://arxiv.org/abs/2601.04889), Jan 2026): LLMs **smooth genuine disagreement into false consensus**. → guardrail: **surface disagreements, don't reconcile**.
- **"MDS through Event Relation Graph Reasoning in LLMs"** (Lei & Huang, Texas A&M, [2506.12978](https://arxiv.org/abs/2506.12978), Jun 2025): feeding the LLM the **event structure** (same-event / sub-event / temporal edges) measurably improves coverage and cuts framing bias vs a raw article bag. → **direct support for the graph lens.**

Crucially, the pipeline **already implements the top two guardrails**: the WRITE stage's
`reporting_varies` field *is* "report where sources disagree, don't reconcile," and its citation
self-check + the COHERENCE stage *are* the cite-or-omit grounding guard.

## The PoC + experiments (run 204, Sonnet via SDK; harness `scratch/cluster-replay/synth_*.py`)

A synthesis prompt embodying all three guardrails (force coverage → `distinct_facts`; preserve
disagreement → `disagreements`; cite-or-omit + a `coherent_event` escape hatch), plus a separate
**faithfulness audit** pass (each fact checked against ITS OWN cited source — COHERENCE-style).

**1. Quality (vs the archived current digest item).** On the 11-article Iran event the synthesis
captured the **48-47 Senate War Powers vote that the current digest item omitted entirely**, and
made the Russia-vs-couple causation dispute explicit instead of smoothing it. Richer and more
neutral — the forced-coverage + disagreement guardrails working.

**2. Faithfulness at scale (5 gold + 5 cheap events, per-fact audit).**

| bundle source | facts | unsupported | rate |
|---|--:|--:|--:|
| gold clusters | 59 | 3 | **5.1%** |
| extract-join (cheap) | 56 | 2 | **3.6%** |

The ~2-5% unsupported are the literature's exact mode — **subtle overstatement, not invention**
("arrested" → "charged"; a hedged claim asserted; a headline phrase quoted as body). **The audit
caught all of them** — so `synthesize → audit` is sound (it mirrors the existing `WRITE →
COHERENCE`). Synthesis is not fabrication-free, but the guardrail works.

**3. Robust to bad bundles.** Extract-join's worst over-merges on run 204 turned out to be
*related facets of the same event lumped* (5 Iran opinion pieces), not unrelated stories —
synthesized fine (0 unsupported, arguably better than gold's split). And on a **deliberately
incoherent bundle** (Iran deal + Russian warship + G7), the safety valve fired correctly:
`coherent_event=False`, "cannot be synthesized into a single coherent narrative" — it **refused to
fabricate** a unifying story. So synthesis degrades safely when the cheap grouping errs.

**4. Cost — roughly neutral as measured, quality is the real win.** Per-event synth+audit ≈
**$0.077/event → ~$1.2 for a 16-story digest** vs current write+coherence **$0.49**. Dropping the
$0.615 Sonnet CLUSTER offsets *most* of the extra → net ≈ cost-neutral (slightly more). The
per-event cache-write overhead is the cost (same "cache-reads dominate" lesson as the cluster
work); a **batched** synthesis (all selected events in one/few calls) would likely tip it back to
a saving. Bottom line: this is a **quality upgrade at ~neutral cost**, not a cost cut.

## Honest limits

- n=1 run (204), small event counts; needs generalization.
- Cost measured per-event (un-batched) — the cost verdict could improve with batching.
- The end-to-end test used the *existing* extract-join clustering; a production graph (soft
  edges, late binding) isn't built. This validated the **synthesis half**, given good-ish bundles.
- Format: the rich synthesis (paragraph + 10 facts + disagreements) is far too long for the email
  as-is — it's a superset to be rendered compactly or passed through a tightening stage (untested).

## Recommendation

The most promising thread from the whole investigation. It converts "cheap clustering has a
modest quality gap" into "cheap grouping **+ a better digest**." Worth pursuing as a **quality**
project, not a cost one. Next, in order: (1) batched synthesis to settle the cost question and fix
cache-inefficiency; (2) a format/tightening stage + eyeball in the email template; (3) generalize
faithfulness across more runs; (4) the actual graph layer (soft membership / late binding) feeding
synthesis. The `synthesize → audit` pair maps onto the existing `WRITE → COHERENCE`, so this is an
evolution of the current pipeline, not a rewrite.

Harness: `scratch/cluster-replay/synth_poc.py` (single-event compare), `synth_experiment.py`
(faithfulness@scale + cost), `synth_junk.py` (over-merge stress), `synth_incoherent.py` (safety
valve). All gitignored scratch.
