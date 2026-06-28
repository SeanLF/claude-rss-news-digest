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

## Method-validation pass (the first cut had circular/eyeballed evidence)

The experiments above had real defects, fixed here:

**Defect 1 — circular faithfulness check (Sonnet audited Sonnet).** **Defect 2 — the auditor
was never validated.** Built a ground-truth-labeled claim set (`audit_validate.py`): 7 claims
genuinely supported by their cited source + 6 deliberately corrupted (wrong numbers, wrong date,
wrong country, a fabricated fact), verified against the real source text. Both judges:

| auditor | recall (corrupted caught) | false-positives (good flagged) |
|---|--:|--:|
| Sonnet (the circular one) | **6/6** | 0/7 |
| Nemotron (cross-family) | **6/6** | 0/7 |

→ the auditor is a trustworthy instrument (not lazy/broken), and a different family agrees on
ground truth. Then re-audited the **real** synthesized facts cross-family (`audit_crossfamily.py`):
over 102 facts, **Sonnet self-audit flagged 4 (3.9%), Nemotron flagged 3 (2.9%)** — Nemotron did
*not* find more, so the ~95-96% grounded result is **not a self-preference artifact** (one fact
in 102 was caught by Nemotron that Sonnet's self-audit missed — tiny, immaterial to the
magnitude). Caveat: my injected errors are blatant; subtle self-preference is bounded by the
cross-family agreement, not separately stressed. (NIM free tier returned 1 ungrammatical response
→ 1 event skipped.)

**Defect 3 — "richer" was eyeballed; faithfulness used a self-selected fact list.** Measured
coverage against an **independent Nemotron-extracted reference** (`coverage_eval.py`): synthesis
covers **79 / 97 / 65%** of the reference facts across three events vs the current item's
**53 / 67 / 25%** — a real **+26-40pp** advantage. **Caveat (do not oversell):** the current item
is *intentionally terse*, so part of the gap is just length; the same-length render-and-remeasure
(the format experiment) is still open, so the coverage win is conflated with length.

**Net:** the *quality* conclusions (faithful ~96%, robust to bad bundles, covers more) survive
cross-family scrutiny and ground-truth validation. The weakest remaining claim is **cost**
(per-event measured ~neutral; *batched* synthesis untested and carries its own quality risk —
more context worsens the skim/positional-bias the literature warns about), plus everything is
**n=1 (run 204)**.

## Generalization across runs (n=4) + closing the open threads (2026-06-27)

Everything above was run 204 (n=1). Re-ran synthesis + faithfulness audit on 3 more runs
(gold bundles; extract-join only materialized for 204):

| run | facts | unsupported | rate |
|---|--:|--:|--:|
| 204 | 59 | 3 | 5.1% |
| 205 | 90 | 4 | 4.4% |
| 208 | 75 | 1 | 1.3% |
| 211 | 83 | 3 | 3.6% |
| **all 4** | **307** | **11** | **3.6%** |

**Faithfulness generalizes** — ~96% grounded across 4 days (range 1.3-5.1%); the flags are all the
same subtle-overstatement mode, each caught by the audit. The `coherent_event` safety valve even
**fired naturally** on run 208 (a gold cluster lumping a heatwave with a France alcohol ban).

**No self-preference (cross-family):** 204 — Sonnet self-audit 4/102 vs Nemotron 3/102; 205
(partial, NIM flakiness) — 2/34 vs 1/34. Nemotron never flags *more* → the ~96% is not a
self-grading artifact.

**Coverage generalizes (n=2, independent Nemotron reference):** run 205 confirms 204 — synthesis
81-100% vs current item 24-33% (1 of 3 events on 205 was a Nemotron measurement failure, flagged
not faked).

**Format/length de-conflation (the "richer = just longer?" question):** compressed each 204
synthesis to digest length and re-scored vs the same reference:

| event | full synthesis | tightened (digest length) | current item |
|---|--:|--:|--:|
| Iran | 89% | **65%** | 49% |
| Warship | 81% | **81%** | 61% |
| Oil | 69% | **47%** | 31% |

At EQUAL length the synthesis-derived brief still covers **+16-20pp** more than the current item.
So the full +26-40pp advantage was partly length, but a real **+16-20pp** survives length control —
"richer" is genuine (the synthesis reads all sources before compressing, so its brief is
better-informed than the current WRITE at the same size).

**Cost (batched, measured):** batching 5 events into one call cut synthesis cost **−57%** ($0.096
vs $0.222) but emitted **−25% facts** (skim) at held faithfulness → a tunable cost/coverage frontier
(per-event = richest/~neutral cost; batched = cheaper/skims; small-group batch = likely sweet spot).

**Cheap-bundle end-to-end now n=2 (closes the main residual).** Regenerated the cheap extract-join
clustering for run 205 (`extract_tags.py` --backend haiku -> `join_materialize.py`; the 205 cheap
clustering is much WORSE than 204's, ARI 0.387 vs 0.661 -- a harder test) and synthesized its
bundles. Result: **4/109 facts unsupported (3.7%)** -- identical to 205's own gold faithfulness
(3.8%) and 204's cheap (3.6%). Worse grouping did NOT degrade synthesis faithfulness. A 43-article
cheap over-merge synthesized coherently (1/34 unsupported), and the `coherent_event` safety valve
fired **a second time in the wild** on a real cheap junk-drawer (a 13-article "JPMorgan restricts
Anthropic Claude access" cluster). Bonus stability check: the 205 gold re-sample (independent second
synthesis of the same articles) gave 3.8% vs the first pass's 4.4% -- faithfulness is run-stable.

**Net after the rigor + generalization pass:** every load-bearing conclusion now holds with n>1
(n=4 gold faithfulness + n=2 cheap-bundle end-to-end), cross-family validation, ground-truth-validated
instruments, and length control. Faithfulness pooled = 96.4%, 95% CI [93.7%, 98.0%] (307 gold facts);
coverage advantage unanimous (8/8, mean +36pp, min +16pp length-controlled). Residual honesty:
cheap-bundle is n=2 not n=4; the 205 coverage had 1 measurement failure; cross-family is full on 204,
partial on 205.

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
