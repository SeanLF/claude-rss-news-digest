# SOTA and competitor re-check, and what the open threads are (2026-08-21)

Prompted by "see what previous sessions were trying to accomplish and if SOTA is moving
underneath us". Baseline is `docs/research/2026-06-24-sota-llm-digest-review.md` (two
months old) and `docs/2026-06-26-news-clustering-prior-art.md`.

## TL;DR

Schema-constrained decoding became reachable from this pipeline, and on measurement it is
**not** the win it looked like: it costs ~2.3-2.7x per call and it converts a detectable
failure into an undetectable one. Recorded here so nobody re-derives the optimistic version.
The other finding is confirmation — the cohesion-gate recommendation in the junk-drawer
findings is a named pattern in the 2025-26 literature, not a bespoke idea.

## 1. Structured outputs: available, verified, and NOT adopted

Anthropic's Structured Outputs compiles a JSON Schema into a grammar and constrains token
generation. From the first-party docs, not a summary: the parameter is
**`output_config.format`**, no beta header is required, and the transitional `output_format`
spelling plus the `structured-outputs-2025-11-13` header keep working for a period. The
supported-model list includes `claude-sonnet-4-6` and `claude-haiku-4-5-20251001` — exactly
the two models this pipeline runs.

The Agent SDK at the pinned 0.2.143 carries `ClaudeAgentOptions.output_format` and forwards
`{"type": "json_schema", "schema": ...}` to the CLI as `--json-schema`. **`run_agent` cannot
pass it through today** — `_build_options` has no such kwarg and `StageResult` has no
`structured_output` field — so adopting it is a wrapper change, not a flag.

### It is hard-enforced, and that is the problem

Negative controls on run 271's real six-claim audit prompt, over a system prompt that says
"N claims means N verdicts":

| schema | result | runs |
|---|---|---|
| `maxItems: 3` on 6 claims | 3 verdicts, ids [1,2,3] | 3/3 |
| `minItems: 12` on 6 claims | 12 verdicts, ids [1..12] | 3/3 |

Enforced in both directions, so `minItems = maxItems = n` would make run 271's shape
structurally impossible. But look at what `minItems: 12` did: the model **invented six
verdicts for claims that do not exist** rather than refusing. Constrained decoding guarantees
the COUNT, never the CORRESPONDENCE.

Applied at the true `n`, that means an auditor not tracking the claims can no longer produce a
detectable shape failure — it is forced to fill the array with something. That is precisely
the silent failure the verdict hardening in `thread_synthesis.py` exists to prevent: shape
compliance without judgment, arriving with `audit_failures` at zero. **The loud failure that
surfaced run 271 would become a quiet one.**

Cost cuts the same way. Cache-controlled medians over 48 replayed calls:

```
thread 556: schema $0.0234 / plain $0.0104 = 2.26x
thread 634: schema $0.0147 / plain $0.0055 = 2.68x
```

At ~7 audit calls a run that is ~$0.07/run extra, every run, against a ~$2.50-3.00 run. The
re-ask fires on a few percent of calls: ~$0.003/run. Roughly 20x the cost to buy a guarantee
about shape while weakening the guarantee about judgment.

**Conclusion: do not adopt `output_format` for the audit.** The deferred todo item
"schema-constrained decoding for the linker" is a different case and may still be worth it —
the linker's failure was a quoted ID, a pure encoding error with no judgment content, which is
exactly what a grammar is good at. Judge it on its own terms; do not carry this section's
availability finding over as an endorsement.

## 2. The cohesion gate is a named pattern, not an invention

`docs/2026-08-01-cluster-junk-drawer-findings.md` §5 recommends a thin cohesion gate on the
~17 SELECTED clusters, pre-WRITE, and explicitly rejects re-opening the entity-conjunction
lever in the join. The 2025-26 literature independently converges on that shape:

- Production clustering systems run a **lightweight LLM-guided module managing cluster
  lifecycle through merging and pruning**, described as critical specifically in production,
  where redundant clusters degrade explainability downstream — i.e. a post-join refine
  stage, not a better join.
- Work on the over-split/over-consolidate trade-off uses a **Dual-Prompt strategy** to
  navigate it, on the finding that relaxed variants over-split and strict variants merge
  distinct topics. That is the same trade-off §2-§4 of the junk-drawer doc measures, and the
  same conclusion: it is not resolvable by tuning one threshold.

This does not validate the gate's effect size — nothing here says it works on our corpus.
It does mean the design is conventional, and the junk-drawer doc's "merge ratchet" framing
(every force pushes toward merging, nothing pushes back) is the recognised failure mode.

Sources are search-summarised and not independently read; cite them properly before they
appear in a design doc.

## 3. Competitors: the differentiator is now contested

- **Digg relaunched in May 2026 as an AI news aggregator**, having abandoned the
  Reddit-style community reboot. It ingests X in real time and does sentiment analysis,
  clustering and signal detection, starting with AI news. The 2026-06-26 prior-art doc's
  Digg teardown is about the *previous* incarnation and its timeline note is now stale.
- **Particle** raised a $10.9M Series A (Lightspeed), ships iOS/Android/web, and its stated
  differentiator is showing how coverage varies by outlet and leaning — which is this
  project's `reporting_varies` and bias-spectrum work, done by a funded team. Being
  second here is fine given the goal is builder recognition rather than reach
  (`project_distribution_builder_recognition`), but "multi-perspective bias-aware digest"
  is no longer an unusual claim, and the README/Show HN framing should not lean on it.
- **Techmeme, NewsCatcher, Event Registry** — nothing found suggesting a change in the
  architecture the prior-art doc recorded. Still the convergent
  cheap-extract → deterministic-join → thin-refine shape.

## 4. What previous sessions left open

Read off `.claude/tasks/todo.md`, the dated docs, and the run record — not re-litigated here.

| Thread | State |
|---|---|
| CLUSTER junk drawers | Measured 2026-08-01, ~23% of shipped clusters bundle 3+ stories. Recommendation written, **not built**. Doc was untracked until today. |
| gnews decoder direction | Resolver fixed and shipped; the stdlib-vs-upstream call and the upstream contribution are still open. |
| Hetzner `degrade` measurement | The one rate-limit number that matters, still unmeasured. |
| `run_health.py` invariants | Shipped `b507577`, **not deployed**. |
| Thread linker pre-filter | Deferred, still open. |
| Move thread linking before WRITE | **Killed on measurement 2026-07-26.** Do not re-propose without new data. |
| Repair phase 2 | Repair covers ~23% of coherence failures; the other 77% is the same failure mode. |
| The Hindu | Resolved today: parked, not dropped. See below. |

## 5. The Hindu: parked, and the email that would un-park it

45 consecutive failed runs (last success run 225, 2026-07-07). Re-confirmed today from the
prod box: 403 on both IPv4 and IPv6 with any User-Agent, 200 from a residential IP — the
2026-07-25 finding of a Cloudflare managed challenge on the Hetzner ASN still holds.

Re-measured 11 Indian alternatives **from prod**, which changed two things the July note
recorded: ThePrint no longer 403s, and Hindustan Times / NDTV / Times of India are all
reachable. None is adoptable — MBFC rates Hindustan Times and NDTV LOW CREDIBILITY with
Mixed factual reporting, and ThePrint right-leaning with poor sourcing, against a catalogue
where everything else is high or mostly-factual. July's conclusion stands: **The Hindu is
the best Indian option and the honest move is to ask them to allowlist us.**

Draft, for Sean to send from his own address (nothing was sent):

> Subject: RSS feed access blocked for a small non-commercial news digest
>
> Hello,
>
> I run a small, non-commercial daily news digest that has included The Hindu's
> international feed (`/news/international/feeder/default.rss`) as its Indian source since
> early 2026. Since 7 July our requests have received a 403.
>
> The block appears to be network-level rather than a policy decision: the same request
> succeeds from a residential connection and fails from our server's IP on both IPv4 and
> IPv6, with any User-Agent. Your robots.txt does not disallow `/feeder/`.
>
> The digest fetches the feed once per day, links back to your articles rather than
> reproducing them, and credits The Hindu by name. It is free, has no advertising, and its
> source list and code are public.
>
> Would you be willing to allowlist our server? I am happy to provide the IP addresses and
> to identify our fetcher with a dedicated User-Agent and a contact URL.
>
> Thank you,
> Sean Floyd

Note the last offer is deliberate: a per-source honest User-Agent was A/B'd on
2026-07-25 and rejected **globally** because it flipped both Haaretz feeds from 200 to 403.
It is only worth applying to The Hindu, and only alongside an actual allowlist agreement.
