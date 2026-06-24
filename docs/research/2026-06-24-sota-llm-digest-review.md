# SOTA Review: LLM Techniques for an Automated News-Digest Pipeline (mid-2025 → 2026-06)

> Generated 2026-06-24 via a research subagent (web-sourced, cited). Scope: techniques
> relevant to the CLUSTER → RECAP → SELECT → WRITE → COHERENCE pipeline, with concrete
> applicability and skeptical assessment. Every claim is cited inline.

A note up front on the system's two biggest stated pain points, because they shape the whole report: (a) COHERENCE over-drops ~25% of accurate headlines because WRITE under-cites, and (b) CLUSTER is expensive (~38% of cost) and resisted cheap embedding replacement (0.497 ARI in the 2026-03 PoC). Both have strong, directly-applicable recent literature.

---

## 1. News summarization / headline faithfulness

**Key findings**

1. **Hallucination detection over modern-LLM summaries is genuinely hard — most detectors sit near chance.** FaithBench (NAACL 2025) was built from summaries where SOTA detectors *disagreed*; most score "near 50% accuracy," and even GPT-4o-as-judge is one of the disagreeing baselines. [arxiv.org/abs/2410.13210](https://arxiv.org/abs/2410.13210), [aclanthology.org/2025.naacl-short.38](https://aclanthology.org/2025.naacl-short.38/) Most important caveat for COHERENCE: a Haiku/Sonnet "judge headline vs source" check operates in a regime where the best models barely clear 80% balanced accuracy. Over-dropping is the *expected* failure mode of a coarse judge, not an anomaly.

2. **Reasoning models are materially better detectors.** On FaithBench, o3-mini-high reached 84.0% balanced accuracy / 82.1% F1 vs GPT-4o's 79.5% / 81.1%. [arxiv.org/abs/2410.13210](https://arxiv.org/abs/2410.13210) The lever for COHERENCE accuracy is a *better/reasoning* judge, not a cheaper one — in tension with moving COHERENCE to Haiku.

3. **Faithfulness-targeted training beats prompting.** Span-level fine-tuning (arXiv 2510.09915, Oct 2025) and entity-coverage control improve faithfulness at the source. [arxiv.org/pdf/2510.09915](https://arxiv.org/pdf/2510.09915) Prompt-level version: constrain WRITE to entities/claims present in source + require inline citation (just started).

4. **Verifiability/abstractiveness trade-off.** More abstractive generations are harder to verify against source (arXiv 2411.17375). [arxiv.org/pdf/2411.17375](https://arxiv.org/pdf/2411.17375) Punchy headlines are abstractive by nature → structurally the hardest field for COHERENCE. Argues for verifying *summary/why-it-matters* claims rather than the headline's exact wording.

5. **HHEM (Vectara) faithfulness ranking** (7,700-doc task): Claude Haiku 4.5 9.8%, Sonnet 4 10.3%, Opus 4 12.0%; GPT-5.4-nano 3.1%, Gemini 2.5 Flash Lite 3.3%, Phi-4 3.7%, Llama 3.3 70B 4.1%, Qwen3 8B 4.8%. [github.com/vectara/hallucination-leaderboard](https://github.com/vectara/hallucination-leaderboard) Counterintuitive: **Claude hallucinates *more* than several small open models on faithful summarization.**

**Applicability**
- **(High/low effort)** HHEM challenges "everything on Sonnet 4.6." For the narrow source-grounded WRITE *summary* field, a small faithful model may hallucinate *less* than Sonnet at lower cost. A/B on the eval floor; the why-it-matters field still needs Sonnet's reasoning — split faithful-summary vs reasoned-why.
- **(High/low effort)** Constrain WRITE: "every entity/number in the headline must appear in the source." Upstream fix for the over-drop.
- **Skeptical:** Headlines are abstractive by design; verify *claims*, not *phrasing*.

---

## 2. LLM-as-judge (why-judge + COHERENCE graders)

**Key findings**

1. **Three biases confirmed/quantified.** Position bias swings pairwise accuracy >10% by swapping order; verbosity and self-preference (favour low-perplexity / own-family outputs) are robust. [arxiv.org/pdf/2410.02736](https://arxiv.org/pdf/2410.02736), [arxiv.org/pdf/2410.21819](https://arxiv.org/pdf/2410.21819) **Self-preference matters here:** a Sonnet why-judge grading Sonnet's why-it-matters is biased toward passing it. "why-judge is the only INDEPENDENT golden" is undermined unless the judge is a *different* family.
2. **Judge-model choice dominates positional bias** more than task complexity/length/quality gap (IJCNLP 2025). [emergentmind.com/topics/llm-as-a-judge-evaluations](https://www.emergentmind.com/topics/llm-as-a-judge-evaluations)
3. **Jury / panel of diverse models reduces idiosyncratic bias**; majority vote across providers recommended. [galileo.ai/blog/llm-as-a-judge-vs-human-evaluation](https://galileo.ai/blog/llm-as-a-judge-vs-human-evaluation) Free NIM GLM-5.1 / Nemotron become useful here (§7) — a different-family juror at zero marginal cost.
4. **Cheap mitigations:** swap-and-average order, static human-labeled calibration set, self-consistency (same input N times; flag inconsistency). [sebastiansigl.com/blog/llm-judge-biases-and-how-to-fix-them](https://www.sebastiansigl.com/blog/llm-judge-biases-and-how-to-fix-them/) A 2025 critique (arXiv 2512.16041) warns judge benchmarks themselves are flawed — trust your own golden set. [arxiv.org/pdf/2512.16041](https://arxiv.org/pdf/2512.16041)

**Applicability**
- **(High/low)** Make the why-judge a *different family* than the writer (GLM-5.1 / Nemotron via free NIM, or Gemini). Removes self-preference bias; fixes the circularity in memory.
- **(Medium/low)** Add swap-order / self-consistency to COHERENCE: if the judge flips pass/fail across two phrasings, don't drop — flag. Directly attacks the 25% over-drop (flaky drops = inconsistent judgments).
- **(Medium/medium)** 2-3 model jury for COHERENCE (Sonnet + GLM-5.1 + Nemotron) with "drop only on majority fail."
- **Skeptical:** Three Sonnet calls is not a jury — value is entirely in cross-family diversity.

---

## 3. Faithfulness / fact-checking vs source (the COHERENCE over-drop bug)

**Key findings**

1. **Decompose-then-verify (FActScore-style) dominates:** split into atomic claims, verify each via NLI (claim=hypothesis, source=premise). [emergentmind.com/topics/factscore](https://www.emergentmind.com/topics/factscore) For COHERENCE: don't ask "is this headline faithful?" — ask "decompose headline+summary into atomic claims; is each entailed by source?" Drop only when a claim is *contradicted*, not when wording diverges.
2. **VeriFastScore (arXiv 2505.16973, May 2025)** collapses extraction + verification into one model pass, much faster than FActScore/VeriScore. [arxiv.org/pdf/2505.16973](https://arxiv.org/pdf/2505.16973) Almost a drop-in COHERENCE design: single cheap-model call doing claim-extract + entailment instead of a holistic vibe check.
3. **Decomposition isn't free.** "Decomposition Dilemmas" (arXiv 2411.02400): decomposition can *hurt* if subclaims lose context; FActScore is "blind to narrative manipulations that montage correct facts in misleading order." [arxiv.org/html/2411.02400v1](https://arxiv.org/html/2411.02400v1)
4. **NLI cross-encoders give a cheap non-LLM layer.** [arxiv.org/pdf/2402.17630](https://arxiv.org/pdf/2402.17630), [arxiv.org/pdf/2509.18901](https://arxiv.org/pdf/2509.18901) An HHEM-class cross-encoder could pre-filter COHERENCE; escalate only borderline cases to the LLM.

**Applicability — highest value for the known bug**
- **(High/medium) Re-architect COHERENCE as claim-level entailment, not holistic judgment.** Decompose → entail each claim → drop only on a *contradicted* claim. VeriFastScore is the blueprint.
- **(High/low)** Combine with §1 WRITE constraint (no entity not in source): attacks the bug from both ends.
- **(Medium/medium)** HHEM/NLI cross-encoder pre-screen so the LLM judge only sees ambiguous cases — cuts cost *and* variance.
- **Skeptical:** Watch the inverse failure — context-loss can let a misleadingly-ordered-but-individually-true headline pass. Keep an adversarial golden set.

---

## 4. Prompt optimization (GEPA PoC)

**Key findings**
1. **GEPA is real and strong.** "GEPA: Reflective Prompt Evolution Can Outperform RL" (arXiv 2507.19457, ICLR 2026 oral). Samples trajectories, reflects in natural language to diagnose failures, evolves prompts on a Pareto frontier. [arxiv.org/abs/2507.19457](https://arxiv.org/abs/2507.19457)
2. **Gains:** +10% over MIPROv2; ~6% over GRPO (RL) avg, up to 20%; **up to 35x fewer rollouts than GRPO.** [arxiv.org/abs/2507.19457](https://arxiv.org/abs/2507.19457)
3. **Instruction-only beats joint optimization and yields ~33% shorter prompts** → lower inference cost. [arxiv.org/html/2507.19457v1](https://arxiv.org/html/2507.19457v1) GEPA's reflection turns *interpretable textual feedback* (your why-judge/COHERENCE reasoning) into prompt edits — exactly the signal this pipeline produces.

**Applicability**
- **(High/medium) GEPA is well-matched.** You already have golden sets, judges that emit textual reasoning, and a regression gate — GEPA's three ingredients. Shorter prompts cut cost.
- **Targets in order:** (1) WRITE (optimize toward why-judge filler-rate), (2) COHERENCE (reduce false drops, leak-count guardrail), (3) CLUSTER (harder; noisier ARI feedback).
- **Skeptical:** Published wins are on crisp scalar metrics; editorial metrics are noisier and partly circular (judge ≈ optimizer). Hold out an independent different-family judge for final acceptance; keep the regression gate strict.

---

## 5. Cheap clustering / story grouping (CLUSTER, ~38% of cost)

**Key findings**
1. **Embedding SOTA jumped past MiniLM.** Qwen3-Embedding tops MTEB (8B: 70.6). The **0.6B** model scores **52.95% avg on 8 English clustering datasets**, designed for resource-constrained hardware (relevant: 4GB Hetzner box). [qwenlm.github.io/blog/qwen3-embedding](https://qwenlm.github.io/blog/qwen3-embedding/), [huggingface.co/Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) The 2026-03 PoC used MiniLM (0.497 ARI); Qwen3-0.6B is substantially stronger — worth re-running the *same* PoC before concluding embeddings can't cluster.
2. **Agglomerative clustering on LLM-grade embeddings is the robust news-event method** (arXiv 2406.10552), LLM only for post-hoc labelling. [arxiv.org/abs/2406.10552](https://arxiv.org/abs/2406.10552)
3. **Hybrid "embeddings cluster, LLM only refines" is the cost sweet spot** — k-LLMmeans (arXiv 2502.09667), HERCULES (arXiv 2506.19992). [arxiv.org/html/2502.09667v1](https://arxiv.org/html/2502.09667v1), [arxiv.org/html/2506.19992](https://arxiv.org/html/2506.19992)
4. **"Text clustering as classification with LLMs"** (arXiv 2410.00927) — classify-into-existing-clusters is cheaper than open grouping. [arxiv.org/pdf/2410.00927](https://arxiv.org/pdf/2410.00927)

**Applicability**
- **(High/medium) Re-run the 2026-03 PoC with Qwen3-Embedding-0.6B/4B + agglomerative** against the same editorial gold. MiniLM is a 2-yr-old encoder; most likely candidate to move the number.
- **(High/medium) Hybrid two-stage CLUSTER:** Qwen3 embeddings + agglomerative → candidate clusters; LLM (Sonnet or free GLM-5.1) resolves only ambiguous boundaries. Even if embeddings alone don't match editorial grouping, they cut LLM input by an order of magnitude.
- **(Medium/low)** Move the CLUSTER LLM step to free NIM GLM-5.1.
- **Skeptical:** Editorial *narrative* grouping is genuinely harder than embedding similarity — the PoC's core finding stands directionally. The win is hybrid (embeddings for recall, LLM for hard merges), not replacement.

---

## 6. Cost reduction for multi-stage pipelines

**Key findings**
1. **Batch API = flat 50% off**, stacks with caching (~95% on repeated portions). [platform.claude.com/docs/.../prompt-caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) Consistent with memory's note that Batch is the only real structural lever (blocked on re-architecting off SDK subagents).
2. **Prompt caching: reads ~10% of base, writes +25%, break-even at 2+ hits.** For a daily digest, the high-value reuse is *within* a run (5-min TTL spans one run, not across days).
3. **Model routing/cascade: 60-80% bill reduction** combining cache+batch+routing. [gmicloud.ai](https://www.gmicloud.ai/en/blog/llm-inference-cost-optimization-caching-batching-routing) The eval floor lets you do this safely: cheap model first, escalate to Sonnet on low-confidence.
4. **Small-model distillation for narrow tasks is viable** — HHEM shows small models out-summarize Sonnet on faithfulness. RECAP and COHERENCE are exactly the narrow tasks small models do well.

**Applicability**
- **(High/low) Move RECAP off Sonnet now.** Title summarization — Haiku/Gemini Flash Lite/GLM-5.1 all suffice; HHEM says small models are *more* faithful here. Lowest-effort win in the report.
- **(High/medium) Cascade COHERENCE:** NLI/HHEM or Haiku first pass → escalate ambiguous headlines to a reasoning judge. Cheaper *and* more accurate than uniform Sonnet.
- **(High/high) Batch API** ~50% structural lever — bigger project.
- **(Medium/low)** Verify within-run prompt caching is actually landing across the 5 stages.
- **Skeptical:** Memory's "cost essentially irreducible" was reached *before* this model data and free NIM access. RECAP-on-Sonnet waste alone contradicts it — re-test. **NOTE (Sean): the 2026-06-18 POC already rejected COHERENCE→Haiku specifically — Haiku rubber-stamps coherence and misses citation-integrity catches. The cascade idea (cheap pre-screen + reasoning escalation) is different from "swap COHERENCE to Haiku" and is the part worth testing.**

---

## 7. Open / free-tier models (GLM-5.1, Nemotron-3-Ultra-550B)

**Key findings**
1. **GLM-5.1** (~April 2026, 754B MoE, MIT). Self-reported 58.4 SWE-Bench Pro — first open model past GPT-5.4 / Claude Opus 4.6. **Caveat: Z.ai self-reported, no independent verification.** [docs.z.ai/guides/llm/glm-5.1](https://docs.z.ai/guides/llm/glm-5.1), [serenitiesai.com](https://serenitiesai.com/articles/glm-5-1-zhipu-coding-benchmark-claude-opus-comparison-2026) GLM-4.6 was a strong open all-rounder at summarization.
2. **Nemotron-3-Ultra-550B** (2026-06-04, 550B hybrid Mamba-Transformer MoE) scores 48 on Artificial Analysis Intelligence Index (most capable open US model), ~6x throughput. [research.nvidia.com/.../NVIDIA-Nemotron-3-Ultra-Technical-Report.pdf](https://research.nvidia.com/labs/nemotron/files/NVIDIA-Nemotron-3-Ultra-Technical-Report.pdf)
3. **Nemotron lineage is specifically strong as a judge.** Predecessor LN-Ultra was the best *open* model on JudgeBench, trailing only o3-mini-high. [arxiv.org/pdf/2505.00949](https://arxiv.org/pdf/2505.00949) → credible *free* juror, different family from Claude.
4. **Open models competitive for batch summarization/classification.** [huggingface.co/blog/.../open-source-llms](https://huggingface.co/blog/daya-shankar/open-source-llms)

**Applicability**
- **(High/low) Use free NIM models as independent jurors**, not primary generators. Nemotron-3-Ultra as why-judge / COHERENCE juror: strong judge pedigree, different family (kills self-preference), free at 40 rpm.
- **(Medium/low) GLM-5.1 for RECAP and CLUSTER-LLM** — structured tasks; free tokens make A/B near-zero-cost.
- **(Don't) Keep GLM-5.1/Nemotron off the WRITE editorial path for now** — editorial voice/region/why-it-matters is where Sonnet matters most and these are least validated for this register.
- **Skeptical:** 40 rpm free is real, but NIM endpoint availability/latency is an operational risk for a pipeline with a prior silent-outage (2026-06-16). Keep as optional jurors / cheap pre-passes with Sonnet fallback, never a hard critical-path dependency.

---

## Top-5 prioritized experiments

1. **Re-architect COHERENCE as claim-level entailment (VeriFastScore-style) + cross-family juror.** Decompose headline+summary → entail each claim → drop only on *contradiction*; add Nemotron-3-Ultra (free) as second juror, drop only on agreement. Targets the 25% over-drop. *High / medium.* [VeriFastScore](https://arxiv.org/pdf/2505.16973), [decompose-then-verify](https://www.emergentmind.com/topics/factscore), [jury](https://galileo.ai/blog/llm-as-a-judge-vs-human-evaluation)
2. **Move RECAP off Sonnet; cascade COHERENCE (cheap pre-screen → reasoning escalation).** *High / low.* [HHEM](https://github.com/vectara/hallucination-leaderboard), [FaithBench](https://arxiv.org/abs/2410.13210)
3. **Re-run CLUSTER PoC with Qwen3-Embedding-0.6B/4B + agglomerative, then hybrid.** The 0.497-ARI conclusion used outdated MiniLM. *High / medium.* [Qwen3-Embedding](https://qwenlm.github.io/blog/qwen3-embedding/), [news clustering](https://arxiv.org/abs/2406.10552)
4. **Run GEPA on WRITE first** (metric: why-judge filler rate, guardrail: COHERENCE leak count), independent different-family judge for final acceptance. *High / medium.* [GEPA](https://arxiv.org/abs/2507.19457)
5. **Make the why-judge a different model family (free NIM Nemotron/GLM-5.1).** Removes self-preference + circularity. *Medium-high / low.* [self-preference](https://arxiv.org/pdf/2410.21819), [LN-Ultra judge](https://arxiv.org/pdf/2505.00949)

**Cross-cutting caveats:** headlines are abstractive (verify claims, not wording); GLM-5.1 benchmarks are vendor self-reported (validate on golden set); GEPA/juries risk circularity when sharing a family with the judge; free NIM endpoints are an availability risk (keep optional + Sonnet fallback); "cost is irreducible" predates current cheap+free models — re-test.
