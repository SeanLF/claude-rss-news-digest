# SOTA and competitor re-check (2026-09-16)

Prompted by "check if any SOTA techniques have come up since our last session" and "check up
on competitors we had previously found to see if there's any ideas we can steal". The last
working session was 2026-09-04 (run 286 review). Baselines: `2026-08-21-sota-and-competitor-recheck.md`,
`2026-08-30-health-check-and-clustering-sota.md`, `2026-06-26-news-clustering-prior-art.md`,
`research/2026-06-24-sota-llm-digest-review.md`.

Relevance was judged against the pipeline's live problems, not novelty: (a) WRITE fabricates
`why_it_matters` from world knowledge and from bleed, (b) CLUSTER junk drawers and over-splits,
(c) SELECT picks are unstable rep to rep (Jaccard 0.24 to 0.34), (d) story threads are "solid,
not remarkable", (e) the bias bar overstates independence, (f) structured outputs rejected for
audits because a grammar guarantees count, never correspondence.

Sources marked **read** had their abstract or page fetched; everything else is
search-summarised and not independently read. Cite properly before any of it appears in a
design doc.

## TL;DR

Two things are worth a cheap PoC, one is worth a label change, and nothing changes a recorded
decision.

1. **SELECT instability has a named, measured cause in the August literature: order
   dependence.** Scorers of equal ranking quality retain sets overlapping only 66 to 84% when
   the candidates are reordered, and no prompt-time change removed it (arXiv 2608.26762,
   2026-08-27, **read**). Our Jaccard 0.24 to 0.34 was measured without separating order
   from sampling. One harness run splits them; if order dominates, order-averaging is the
   inference-time mitigation the paper leaves open.
2. **The absence-vs-contradiction split the 2026-08-30 doc wanted is a published three-way
   label** ("Out-Dependent", VeriGray, **read**). Making COHERENCE emit it costs a prompt line
   and gives the measurement instrument for problem (a) that we do not have.
3. **The competitor field moved toward us, not away.** Kagi News is source-available with
   public feed lists, World Monitor is AGPL-3.0 with 461 feeds and an MCP, and Pollar ships an
   MCP server and topic threads. "Transparent, self-hostable, MCP-addressable digest" is no
   longer a differentiator on its own. What none of them publish is the fact-check: which
   claims were checked, which failed, what was repaired or dropped. That is the one thing
   still ours, and the README and any Show HN framing should lead with it.
4. **Agent SDK 0.2.150 to 0.2.153 are bundled-CLI bumps plus one option we do not use.**
   Nothing there changes a recorded decision.

## 1. SOTA since the last check

| Paper | Date | New to us | Problem | Verdict |
|---|---|---|---|---|
| Order-consistent LLM scorers (2608.26762) | 2026-08-27 | yes | (c) | **PoC**: measure order vs sampling share of SELECT drift, one harness run |
| The Gray Zone of Faithfulness / VeriGray (2510.21118) | 2025-10, v4 2025-12 | yes | (a) | **Adopt the label**: third COHERENCE verdict "out-dependent" for why_it_matters |
| NTS-CoT timeline summarisation (2606.13171) | 2026-06-11 | yes | (a), (d) | ignore: its Causal-CoT infers connective tissue, which is exactly what fabricates |
| TimelineReasoner (2605.12518) | 2026-04-03 | yes | (d) | ignore now; note the shape (gap-driven retrieval) if threads are ever made remarkable |
| Self-correcting news summaries with external knowledge (2506.19607) | 2025-06 | yes | (a) | ignore: imports outside knowledge, which the grounding rule forbids |
| Hierarchical Matryoshka news clustering (2506.00277) | 2025-06 | yes | (b) | ignore: a better encoder does not fix the gate's metric, which failed a negative control |
| Ensembles for categorisation, CoRE (2511.15714, 2510.13143, EACL 2026) | 2025-10 to 2026 | partly | (c) | fold into the PoC above; jury idea already recorded 2026-06-24 |

### 1.1 Order dependence is a measured cause of ranking instability (PoC)

[arXiv 2608.26762](https://arxiv.org/abs/2608.26762), *Equal Ranking Quality, Different
Decisions: Training Order-Consistent LLM Scorers*, 2026-08-27, **read**. Five trained scorers
with near-identical ranking quality retained document sets overlapping only 66 to 84% when
the candidates were presented in a different order. The decision flip rate under permutation
was 0.149 to 0.164 for baselines. "No prompt-time change we test removes that order
dependence." Their fix is training (order-consistency SFT), with order-averaged distillation
as the runner-up.

What transfers: not the fix, since we cannot fine-tune Claude, but the measurement. Our
SELECT instability (Jaccard 0.24 to 0.34 rep to rep, `2026-09-03` stage-invocation plan) was
measured by re-running on the same input in the same order, so it conflates sampling noise
with order dependence. The paper says order dependence alone can move retained sets by a
third. The experiment is one harness run: N reps in a fixed cluster order versus N reps with
`clusters.json` shuffled, same run artifact, compare within-arm Jaccard. If the shuffled arm is
materially worse than the fixed arm, order is a real component and the mitigation the paper
points at is order-averaging at inference: run SELECT k times over permuted input and take the
intersection or a vote. At $0.38 a SELECT call (run 298's prod row; the harness's own fifteen calls averaged $0.42) that is about $1.15 to $1.25 a run for k=3.
Do not build the mitigation before the measurement; the 2026-07-26 delta PoC showed control
noise can exceed every claimed effect here.

**Measured the same day: real but small.** Three arms (archived, shuffled, size-sorted), five
reps each, on run 298. Order does not change SELECT's self-consistency (within-arm Jaccard
0.51 / 0.47 / 0.55, no gap significant, power adequate for a paper-sized effect) but it does
move about one of 16 picks (0.6 to 1.3) to different clusters (within-minus-cross shift test,
p = 0.008), well under the paper's 16-34%. Order-averaging would cost ~$1.25/run at k = 3 to stabilise
roughly one should_know pick; recorded as a costed option. A size-sorted order is free and
matched or beat the archived order on every measure on one day. Harness `bin/eval-select-order`
(gap and shift tests built in); write-up `docs/2026-09-16-select-order-dependence-poc.md`.

Related, search-summarised only: [Majority Rules](https://arxiv.org/pdf/2511.15714) (LLM
ensembles for content categorisation reach or exceed human-annotator consistency),
[Stable LLM Ensemble](https://arxiv.org/pdf/2510.13143), and
[CoRE](https://aclanthology.org/2026.findings-eacl.182/) (EACL 2026 Findings, consistency-weighted
ensembling). All three are the jury idea the 2026-06-24 review already recorded; nothing new
to adopt separately.

### 1.2 A three-way faithfulness label matches the failure we already see (adopt the label)

[arXiv 2510.21118](https://arxiv.org/abs/2510.21118), *The Gray Zone of Faithfulness: Taming
Ambiguity in Unfaithfulness Detection*, v4 2025-12-29, **read**. Existing faithfulness
benchmarks disagree on whether a sentence that needs outside knowledge to verify is a
hallucination. The paper introduces a third category, **Out-Dependent**, for statements that
are neither entailed nor contradicted by the source, and builds the VeriGray benchmark on the
three-way label. Around 9% of generated summary sentences fall in that band across models;
GPT-5 hallucinates outright in about 6%.

This is the split the 2026-08-30 health check asked for and could not get from
`coherence_report.json`: run 280's `why_it_matters` failures were absence, not contradiction,
and four of six claims were absent from the entire article pool. Today COHERENCE returns
pass/fail, so absence and contradiction are one number and the repair path treats them alike.
Adding `verdict: out-dependent` alongside `pass` and `contradicted` in `coherence.md`'s output
costs a prompt line and a schema field, and gives three things: a per-run count of
world-knowledge fabrication, a way to see whether repair's regenerate-from-cited-sources fixes
out-dependent claims at a different rate than contradicted ones, and a cleaner planted-error
eval (the planted fixtures can be labelled the same way). The policy does not change: the
project rule is "don't fabricate details not in the RSS summary or fetched article text", so
out-dependent still fails. This is an instrument, not a fix. Cost: one prompt edit, one test,
one eval re-run. Note the 2026-08-30 lesson that naming an error class in a prompt does not
fix it; this names it in the *output*, which is different.

**Measured the same day:** the label is emitted on 13/13 failed fields over two runs, recall
and false-drops unchanged, kind agrees with the label-type mapping on 4 of 5 hard positives
(the fifth, a fabricated causal link, is arguably a contradiction and the mapping now abstains
on that type). Write-up `docs/2026-09-16-coherence-failure-kind-poc.md`; adoption is a todo
item. The value is spelled `unsupported`, not `out-dependent`.

### 1.3 Ignored, with reasons

- **NTS-CoT** ([arXiv 2606.13171](https://arxiv.org/abs/2606.13171), 2026-06-11, **read**).
  Three modules: Element-CoT extracts news elements before summarising, Date Selection picks
  timestamps, Causal-CoT "infers causal relationships to reduce omissions". The first is what
  our cited-ids design already forces; the third is the mechanism behind our worst failure
  (WRITE reaching for causal connective tissue from priors). Adopting it would push the wrong
  way.
- **TimelineReasoner** ([arXiv 2605.12518](https://arxiv.org/abs/2605.12518), 2026-04-03,
  **read**). A reasoning-model loop with an event memory, a Timeline Updater and a Supervisor
  that "identifies information gaps and refines timelines through targeted document retrieval".
  Our thread synthesis has the memory (recent deltas) and the updater (EVOLVE plus per-fact
  audit) but nothing that turns gaps into retrieval: the open-question ledger raises 11x faster
  than it resolves (1,798 open, 2026-08-30) because nothing acts on a question. If threads are
  ever made remarkable rather than solid, gap-driven retrieval into the day's pool is the
  published shape. Not now; the thread question is a rendering question this session.
- **Self-correcting with external knowledge** ([arXiv 2506.19607](https://arxiv.org/abs/2506.19607),
  FEVER at ACL 2025, **read**). Generates verification questions and answers them from search
  engines. That is world knowledge by construction; our repair regenerates from cited sources
  only, and must.
- **Hierarchical Matryoshka clustering** ([arXiv 2506.00277](https://arxiv.org/html/2506.00277v1),
  2025-06, search-summarised). Multilingual embeddings clustered level-wise so story and
  narrative granularity are separate cuts. The granularity knob is the junk-drawer versus
  over-split trade-off, but the embed gate was rejected because its metric was maximised by
  fragmentation, and the 4 GB box rules out BGE-M3-class encoders. A different encoder does
  not rescue a metric that failed a negative control.
- **LLM-enhanced clustering on GDELT** ([arXiv 2406.10552](https://arxiv.org/abs/2406.10552))
  and **EpiMine** ([arXiv 2408.04873](https://arxiv.org/pdf/2408.04873)) are already in the
  2026-06-26 prior-art doc.

### 1.4 Anthropic surface: nothing changes a recorded decision

**Claude Agent SDK** ([CHANGELOG](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md),
**read**), pinned at 0.2.149 in `constraints-prod.txt`:

| Version | Change |
|---|---|
| 0.2.150 | bundled CLI 2.1.257 |
| 0.2.151 | bundled CLI 2.1.258 |
| 0.2.152 | bundled CLI 2.1.259 |
| 0.2.153 (2026-09-15) | bundled CLI 2.1.273; `snapshot` on `SystemPromptPreset` keeps the first request's system prompt across resumed sessions for cache hits |

We run one-shot stages and never resume, so `snapshot` is inert here. The bump is a routine
one for the deps task, and `test_sdk_pin.py` will force `bin/sdk-canary` as designed.
Out of scope for this doc, one sentence: PyPI now says the CLI is bundled with the package, so
the image build may be installing a CLI it no longer needs, or shipping two.

**Messages API** (citations on document blocks, `output_config.effort`, mid-conversation
system messages, structured outputs). The native `citations` feature is the first-party form
of our cited-ids design and would make the coherence check a lookup instead of a judgement.
It is Messages-API only. The pipeline runs on the Agent SDK under the subscription, and the
economics decision is recorded (`project_durable_execution_and_thread_entity`: "Messages API
= leaving the subscription"). Nothing here reopens it. The 2026-08-21 finding on structured
outputs stands unchanged.

## 2. Competitors

The 2026-08-21 verdict, that "multi-perspective bias-aware digest" is no longer an unusual
claim, now extends to "transparent, source-available, MCP-addressable". Three of the seven
below ship at least two of those. Ordered by how much they overlap with what we do.

| Product | Story continuity | Transparency | Open | Verdict |
|---|---|---|---|---|
| Kagi News | per-story timeline and background (unverified) | cited sources per story, community feed lists | code on GitHub | **adapt** the story-page shape for `/thread/{id}`; the direct builder-recognition rival |
| Pollar (Sean-named) | topic threads with freshness stamps, Live timelines | numbered source list per event, no lean labels | MCP server, not open | **adapt**: link the digest into `/threads`, show a freshness stamp |
| World Monitor (Sean-named, confirmed) | 7-day country timelines, not per story | 748 attributed providers, corroboration-gated alerts | AGPL-3.0, MCP | **ignore** the map; **adapt** corroboration-by-independent-origin as a reader-facing signal |
| Particle | per-story timeline, follow story | Reality Check per claim, outlet lean | no | ignore, unchanged since 08-21 |
| Ground News | timeline view, Blindspot | factuality, ownership data (Vantage) | no | **adapt later**: outlet ownership on the bias bar |
| Digg | none, ranks by acceleration | source voices shown | no | ignore, different input |
| Techmeme | lead plus corroboration | outlet count | no | ignore, unchanged |

### 2.1 Kagi News: the one to watch for builder recognition

[news.kagi.com](https://news.kagi.com/) (**read**, homepage only). Free, "160887 news today",
categories including "Today in History", a "Contribute" link to GitHub, an RSS feed for
subscribers, "Updated 2 hours ago". The Readless comparison (search-summarised,
[readless.app](https://www.readless.app/blog/best-ai-daily-news-briefing-tools-2026)) calls
it "one cited press review per day from thousands of community-curated sources".

From prior knowledge and not confirmed on this fetch: a Kite story page carries Highlights,
Perspectives grouped by outlet, Historical background, Timeline of events, Quick questions,
and a Sources list. If that holds, it is the story-page shape our `/thread/{id}` page lacks
and the 2026-07-08 note said was the unlock ("a synthesized story so far atop each thread, fix
the delta so it shows change not restatement, prune dormant"). Verdict: adapt the shape, not
the product. Also the honest comparison for any Show HN: they are the incumbent open daily
digest, so the pitch cannot be "open daily digest". It has to be what they do not show.

### 2.2 Pollar (named by Sean from memory)

[pollar.news/en](https://pollar.news/en) (**read**), Pollar Prosta Spółka Akcyjna, Kraków,
registered 2025-09-16 ([Grokipedia](https://grokipedia.com/page/Pollar_News), search-summarised,
treat as unverified). English, Polish, German, French. Free, no ads, memberships at $2.99 and
$6.99 a month. "Pollar does not publish original reporting or opinionated takes"; it clusters
articles into events by hierarchical clustering and links back to the originals.

What they do around continuity that we do not: **topic threads** as a first-class object with
freshness stamps ("Demography and migration, Updated 2m ago"), a **Live** section with
dedicated timelines for developing stories, and a **Daily Brief** that opens with a
cross-story theme sentence. An event page (**read**,
[tiktok-targets-ai-slop](https://pollar.news/en/event/tiktok-targets-ai-slop)) is an overview
with subsections ("Industry moves and past measures", "What's next"), a location tag, a source
count, and a numbered source list with outlet and date. No update history, no perspectives
section, no lean labels: changes are folded into the narrative. They also ship an MCP server
([mcp.pollar.news](https://mcp.pollar.news/)) and an "Ask Pollar" chat, which is parity with
our 2026-09-02 MCP surface and `/ask`.

Verdict: adapt one thing. Our threads exist and have an index at `/threads`, and the daily
digest does not link to either. Pollar's threads are reachable from every surface and carry a
freshness stamp. That is the whole gap for the "links to threads from the daily digest"
question this session is asking: a link on the "Ongoing · day N" eyebrow, and a "Threads"
entry in the issue chrome. Ignore the markets, the map, and the live section; they are a
different product.

### 2.3 World Monitor: "the huge map with many signals"

Sean described a platform with "a huge map and many other signals" from memory and confirmed
the identification below in-session (2026-09-16). The search also turned up other live-map products ([AI World Monitor](https://mwm.ai/apps/ai-world-monitor-live-map/6760740707),
[The World Now](https://www.the-world-now.com/live-world-map),
[WorldLens Live](https://www.worldlens.live/)). The one he meant is
**World Monitor** ([worldmonitor.app](https://www.worldmonitor.app/), **read**): 57 map
layer types, "461 feeds from 748 attributed providers", GDELT events, AIS vessels, military
flights, 13 chokepoints, 86 subsea cables, GPS jamming, BGP anomalies, markets, prediction
markets.

What is relevant to us, and it is not the map:

- **Alerts gated on corroboration.** "Breaking banners fire only when independent origin
  types corroborate", naming news classification, keyword velocity, hotspot escalation and
  official sirens as distinct origin types. That is the independence idea behind our bias bar,
  which the 2026-08-26 audit found overstates independence because 33% of multi-outlet
  stories carry a near-duplicate pair. They count origin *types*; we count outlets. Adapt:
  when the bias bar is fixed (persist `entry.author`, the recorded fix), the reader-facing
  claim should be "N independent accounts", not "N outlets".
- **Country briefs with cited sources and 7-day country timelines.** Continuity by place,
  not by story. Ignore; our unit is the story.
- **AGPL-3.0 on GitHub, free tier with no signup, Pro at $39.99 with MCP and API access.**
  A second open, MCP-addressable news system in the field. Same conclusion as 2.1.

### 2.4 Particle, Ground News, Digg, Techmeme: nothing new to steal

- **Particle** ([particle.news](https://particle.news/), **read**, homepage). September 2026
  release notes (search-summarised) add regional election feeds, long-read audio with a mini
  player, faster podcast clips. Story cards show article counts up to "809+ sources" and
  consequence framing in the dek. The 2026-08-21 verdict stands: funded team, same
  reporting-varies idea, do not lean on it.
- **Ground News** ([help article](https://help.ground.news/en/articles/648513), **read**).
  Premium: factuality ratings, Blindspot, headline comparison. Vantage: "Ownership data for
  thousands of news outlets and Bias Insights", "My News Bias". A timeline view exists per a
  search snippet ([stationx review](https://www.stationx.net/ground-news-review/)) but the
  help page does not list it. Adapt later: an `owner` field in `sources.json` is a static
  table, and ownership concentration is the honest complement to a bias bar. Not this session.
- **Digg** ([TechCrunch 2026-05-11](https://techcrunch.com/2026/05/11/digg-tries-again-this-time-as-an-ai-news-aggregator/)).
  Still the AI-news vertical, ranking by acceleration across ~1,000 voices ingested from X.
  No story pages, no continuity. Different input; ignore.
- **Techmeme**: unchanged.
- **Readless** (search-summarised): one editorial briefing from the newsletters you already
  follow. Personal-input digest, different problem; ignore.

## 3. What is still ours

None of the seven publish the check. Kagi cites, Pollar counts sources, World Monitor gates
alerts on corroboration, Particle runs a per-claim Reality Check but shows a verdict, not the
work. Nobody shows the reader which claims were checked against which source, which failed,
which were repaired from their own citations, and which were dropped. We have that data per
run (`coherence_report.json`, the repair log, `run_health` invariants) and do not surface it
anywhere a reader can see. For the builder-recognition goal that is the lead, ahead of
"transparent" and "self-hostable", which are now table stakes.

## 4. Open questions this doc raises

1. The Kagi story-page sections are from prior knowledge; open one story and check before
   the thread-page design borrows from it.
2. ~~The SELECT order-dependence PoC needs a harness~~ Built and run twice the same day
   (`bin/eval-select-order`, `docs/2026-09-16-select-order-dependence-poc.md`): a small
   real order component (about one of 16 picks), no change in self-consistency. Order-averaging
   is a costed option at ~$1.25/run; a size-sorted order is the free follow-up; both need a
   second day.
