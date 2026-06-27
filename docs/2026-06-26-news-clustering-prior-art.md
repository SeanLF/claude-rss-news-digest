# News-event clustering — prior art + redesign direction (2026-06-26)

Lit + production teardown to ground a CLUSTER redesign, prompted by two observations:
(1) holistic LLM clustering is ~38% of pipeline cost and the draft-injection cost lever
just failed (`2026-06-25-cluster-draft-refine-ab-results.md`); (2) our clustering is
"binary" — each article forced in/out of one story — and that rigidity is exactly where
quality breaks (bridge articles, facet lump/split). Four parallel research strands
(SOTA/CDEC, production aggregators incl. Digg, soft-membership, cheap architectures).
All four independently land on the same design. Sources inline.

## TL;DR

- **The cheap structured baseline is most of the way to SOTA, and it's what everyone
  actually ships.** On the Miranda news-stream benchmark (BCubed F1): dense neural
  embeddings *alone* = **69**; TF-IDF over tokens/lemmas/**entities** *alone* = **86**;
  **+ publication-time = 92**; best hybrid (= +entity-embeddings) = **94.8**
  ([Saravanakumar 2021](https://arxiv.org/abs/2101.11059)). Entities + time, joined
  deterministically, get you to ~92 with **zero LLM calls**. Embeddings are an additive
  signal, never a replacement. This reframes our prior "MiniLM 0.497 ARI, can't replace
  Sonnet" finding: embeddings fail *as a standalone editorial clusterer*, not as the
  cheap **join layer** under a thin LLM refine — which is the trick every serious system
  uses.
- **The "binary in/out" fix is NOT soft clustering** (FCM/GMM/overlapping community
  detection don't demonstrably win on news and are a tuning burden on a 4 GB box). It's a
  **hard story spine + a soft multi-label layer**: keep one canonical cluster per article
  for dedup, but tag each article with several entity/event labels so a bridge article
  ("oil rises on the Iran war") informs *both* stories without being shown twice. This is
  literally how Event Registry works (hard events + a parallel entity index for the
  cross-cutting view).
- **Granularity (the facet lump/split that wrecks our ARI) is a known, named problem**
  ("quasi-identity" / event granularity, [Hovy 2013](https://www.cs.cmu.edu/~hovy/papers/13HLT-Events-workshop.pdf))
  with a clean knob: a single agglomeration threshold tuned to F1, or an explicit
  **episode hierarchy** (episode = core entities + action + time + location; a "key event"
  = a sequence of episodes — [EpiMine, ACL 2025](https://arxiv.org/abs/2408.04873)). That
  models "congressional vote" vs "diplomatic reaction" as separate episodes of one story —
  a *tunable join policy over structured facets*, which an embedding distance can't express.
- **Convergent architecture (Google News, Particle, NewsCatcher, Event Registry, Miranda/
  Priberam all do versions of this):** cheap per-article structured extraction → deterministic
  entity+time join → thin LLM only on borderline cases + labels. This is the cost lever
  draft-injection wasn't: Sonnet's input shrinks from 465 raw articles to ~50 cluster
  gists.

## The bar to beat (for a PoC)

- **News-stream story clustering (closest analog): ~92 BCubed F1 is the bar, ~94.8 is
  SOTA** — and **entity-bag + publication-time alone = 91.7** with no LLM
  ([Saravanakumar Table 1](https://arxiv.org/abs/2101.11059)). PoC target: a cheap
  per-article entity/action/time extraction + deterministic join should clear ~90; if it
  does, we're at pre-LLM-SOTA parity at near-zero cost, and the LLM only arbitrates the
  residual borderline pairs.
- **CDEC reference (ECB+, CoNLL F1):** same-head-lemma baseline = **76.5**, strong 2021
  neural = ~85, 2025 SOTA = **88.4** ([ACCI](https://www.nature.com/articles/s41598-025-32765-6)).
  The entire neural/LLM frontier is a ~12-point climb over a dumb lemma baseline — the
  ceiling is real but shallow, and the strongest recent CDEC systems are themselves
  **extract-per-mention → link** (distilled rationales, [Nath NAACL 2024](https://aclanthology.org/2024.naacl-long.218/);
  LLM-summarize-then-link, [Synergetic 2024](https://arxiv.org/html/2406.02148v1)), not
  holistic end-to-end — for cost and interpretability.
- Our own metric is ARI vs gold, where Sonnet's from-scratch re-run noise floor is **~0.75**
  (today's A/B). The PoC should be scored on both ARI (continuity with prior PoCs) and
  BCubed/pairwise-F1 (continuity with the literature).

## What the production systems actually do

| System | Grouping approach | Source |
|---|---|---|
| **Google News** | TF-IDF + cosine + **agglomerative hierarchical**; modern stack adds transformer NLP + **entity recognition**; cross-publisher coverage count drives prominence | [SEL](https://searchengineland.com/google-news-ranking-stories-30424) |
| **Particle.news** (ex-Twitter) | semantic **embedding clustering**; story only forms at **≥3 articles / ≥2 publishers** (diversity gate); per-claim "Reality Check" verification | [TechCrunch](https://techcrunch.com/2024/11/12/particle-launches-an-ai-news-app-to-help-publishers-instead-of-just-stealing-their-work/) |
| **NewsCatcher** (concrete recipe) | embed title+content (Qwen3-Embedding-0.6B) → cosine edges > **0.7 threshold** → **Leiden community detection** → clusters (threshold = precision knob) | [docs](https://www.newscatcherapi.com/docs/v3/documentation/guides-and-concepts/clustering-news-articles) |
| **Event Registry** (~200k art/day) | hard event clustering **+ a parallel entity-tag layer**; cross-cutting view comes from the entity index, not overlapping clusters | [blog](https://www.blog.eventregistry.org/harnessing-ai-and-nlp-how-event-registry-transforms-global-news-into-actionable-insights/) |
| **Miranda/Priberam** (the recipe to copy) | **online centroid** clustering; merge decision is a **learned SVM over a similarity feature-vector** (TF-IDF cosine on tokens/lemmas/entities + 3 time-decay features, σ=72h), not a scalar threshold | [arXiv 1809.00540](https://arxiv.org/abs/1809.00540) |
| **Feedly** | **~80% of incoming articles are dups**; SimHash/LSH near-dup collapse → cluster only ~1/5, propagate to dups (biggest cost lever) | [eng blog](https://feedly.com/engineering/posts/reducing-clustering-latency) |

**The single most useful engineering number:** Miranda's learned merge classifier scored
**94.1 F1 vs 82.8 for a grid-searched scalar cosine threshold** — an ~11-point gain purely
from replacing "one tuned number" with "a tiny classifier over a handful of similarity
features (cosine + entity-Jaccard + hours-gap)." If we do a deterministic join, the
decision should be a learned 2–6-feature logistic/SVM, not a hand-set threshold.

## Digg teardown (the requested target)

**Timeline correction (high-confidence, press-sourced):** the social/community Digg reboot
(open beta Jan–Mar 2026) was **shut down after ~2 months** — no product-market fit + an
"unprecedented bot problem." Digg **relaunched May 2026 specifically as an AI news
aggregator**, starting with the AI-news vertical — so the *current* Digg is directly our
use case ([TechCrunch 2026-05-11](https://techcrunch.com/2026/05/11/digg-tries-again-this-time-as-an-ai-news-aggregator/),
[Fast Company](https://www.fastcompany.com/91540767/digg-is-back-again-this-time-as-an-ai-news-aggregator)).

**Their model (press-confirmed pipeline; internals inferred — no first-party eng blog
exists):**
- **Social-first ingestion, not RSS:** real-time ingest from X, monitoring a **curated set
  of ~1,000 "most thoughtful voices in AI"** (researchers, founders, investors, media).
- **Pipeline stages named in coverage:** sentiment → **topic clustering** ("AI news
  fragments into many posts about the same underlying development" → group into one story)
  → **signal detection** (meaningful attention shift vs background chatter) → **velocity/
  acceleration ranking** ("importance and acceleration, not recency").
- **Inferred mechanism:** a large part of Digg's "clustering" is likely **link/entity
  co-citation across the trusted source set** (many trusted accounts pointing at the same
  URL/event = the cluster signal), with embeddings as same-story glue — cheaper and more
  robust than holistic LLM clustering. *Observed UI:* one development = one unit with
  multiple sources/voices attached (genuinely soft, multi-source), ranking decoupled from
  grouping.

**Portable lessons:** (1) **decouple grouping from ranking** — group cheaply, rank
separately; (2) **co-citation / independent-source-count is itself a clustering AND
salience signal**; (3) the unit is "a development," with sources attaching to it (soft,
multi-source) — not one row per article.

**Techmeme** (closest analog — human+algorithm tech-news clustering, 20 yrs): full-text
crawl (not just headlines); cluster = **lead item + corroboration + commentary**; **count
of independent outlets** both confirms a cluster is real and ranks it; lead chosen by
authority/earliest; a **thin human/correction layer** sits on top (their estimate: ~1000:1
algorithmic:human edits, but the human layer is what keeps quality — their honest 2008
verdict: "automated news doesn't quite work" alone). Borrow: independent-source-count as a
confirmation+salience signal, algorithmic-cluster + light LLM correction rather than
holistic LLM grouping ([Techmeme 20-years](https://news.techmeme.com/250912/20-years)).

## The "binary in/out" answer: hard spine + soft label layer

The honest finding (strand 3): **no strong evidence that a soft/fuzzy clustering
*algorithm* beats hard clustering on news.** FCM/GMM collapse in high dimensions and need
dimensionality reduction; overlapping community detection has weak news ground-truth and is
machinery to babysit on a 4 GB box. The bridge-article win comes from **features/labels,
not a fuzzy partition**:

- Keep a **hard canonical cluster** (the dedup spine — answers "what story is this, for
  not-showing-it-twice").
- Add a **cheap per-article multi-label tag pass** → `{entities[], event_phrases[1-3],
  primary_event}`. An article carries "Iran war" *and* "energy markets." Open-domain
  (novel daily stories, no taxonomy) is handled by **extract-then-canonicalize**: the LLM
  *generates* candidate labels (5W1H-style), then labels are **canonicalized within the
  day's batch** (string/embedding match), so the label vocabulary is *emergent per day*.
- **Display/dedup via a deterministic tie-break ladder:** primary story = (1) the
  LLM-assigned `primary_event`; tie → (2) highest salience (in headline/lede, most articles,
  entity centrality); tie → (3) higher tier (must_know > should_know); tie → (4) earliest
  RSS timestamp. Extra labels become "also relates to →" cross-links and feed dedup
  ("this oil story is already covered under Iran war").

Caveat the agents were honest about: **nobody publishes an F1 specifically on bridge-article
recovery** — this is a well-supported design pattern, not a measured delta. We must prove it
on our own broken cases.

## Proposed architecture for our pipeline

Replace the single holistic Sonnet CLUSTER call with a 4-stage pipeline (this is the
convergent design, adapted to our cost/compute constraints and our prior findings):

- **Stage 0 — near-dup collapse** (CPU, ~free): SimHash/MinHash + LSH over title+lead;
  fold exact/near-dup wire copies into one representative, carry the dup list. `datasketch`
  in Python. Biggest cost lever, O(n).
- **Stage 1 — per-article structured extraction** (Haiku, ~465 tiny *parallel* calls):
  `{entities[canonical org/person/event/product], event_type, geo, salient_keywords[≤8],
  event_phrases[1-3], primary_event, one_line_gist}`. This is where the heavy reasoning
  moves — off one O(all-articles) Sonnet call onto cheap, independent, linear-scaling Haiku
  (same register as RECAP/COHERENCE). Multi-label by construction.
- **Stage 2 — deterministic join** (CPU, ~free): online centroid clustering à la
  Miranda; per-article similarity = entity-Jaccard + event_type/geo match + keyword/static-
  embedding cosine + Gaussian time-decay (σ≈72 h, gives cross-day tracking for free).
  Decision via a **small learned classifier** over those features (trained on a few hundred
  labelled pairs from our archive), **not** a hand-set threshold. Block on shared-entity +
  2-day window to prevent semantic over-merge. Granularity knob = the trained threshold,
  tuned to F1 on the golden set; bias slightly toward **over-segmentation** (safer for
  Sonnet to merge than split — today's A/B showed refine under-merges).
- **Stage 3 — Sonnet refine + label only** (small): hand Sonnet ~40–60 cluster **gists**
  (not 465 raw articles) to merge/split the handful of borderline clusters and write story
  labels. Input shrinks massively → cuts the 38% cost line, keeps editorial judgment in the
  loop (respecting "embeddings can't match Sonnet's editorial grouping standalone").

Cheap-embedding option that fits a 2 vCPU/4 GB box where ONNX-MiniLM strained:
**Model2Vec (`potion-base`)** — 8–30 MB, no GPU, ~15k sentences/s on CPU, ~89–92% of
MiniLM's MTEB ([Model2Vec](https://www.xugj520.cn/en/archives/model2vec-fast-static-embedding.html)).

## How this resolves today's failed cost lever

Draft-injection (Arch-2) failed because the draft anchored Sonnet to sub-baseline quality,
and a *good* draft (DeepSeek-batched) couldn't be produced — production chunking can't
co-locate story-mates without peeking at gold. **This design sidesteps that entirely:** the
cost saving doesn't come from a cheaper clustering of the same task; it comes from
**changing Sonnet's input** — it reasons over ~50 pre-built gists instead of 465 raw
articles. The draft here is built from per-article *understanding* (entities/events/time),
which the literature puts at ~92 F1 — far above the 0.69 embedding / 0.67 DeepSeek-chunk
drafts that failed. And Stage 3 only refines borderline clusters, so it's a bounded call,
not a re-cluster.

## Honest caveats / what must be PoC'd before any build

1. **No bridge-article F1 in the literature** — prove the label layer fixes our *measured*
   boundary failures by replaying the cases where hard CLUSTER broke (we have
   `scratch/cluster-replay/` + run 204/205 gold).
2. **The 92-F1 evidence is BCubed on Miranda media-monitoring**, a close analog but not
   "editorial digest stories with ARI." Re-measure on our own data.
3. **Arch-2 anchoring risk still applies to Stage 3** — measure the Stage 1–2 draft's
   ARI/F1 against gold *before* wiring Sonnet to trust it; keep the over-segment-then-merge
   bias.
4. **Stage 1 extraction quality is the new linchpin** — entity NER is noisy on orgs
   ([TDS](https://towardsdatascience.com/clustering-news-articles-based-on-named-entities-306a23d368e1/));
   a Haiku extraction pass must be validated (does it reliably emit the right entities +
   event for our feeds?).

## Recommended first PoC (cheap, offline, decisive)

Before any architecture work, one afternoon against real data (no Sonnet spend on the
gate):

1. Run a **Haiku Stage-1 extraction** over run 204/205 articles → per-article
   `{entities, event_type, time, keywords, primary_event}`. (Haiku, cheap, parallel.)
2. Implement the **Stage-2 deterministic join** (entity-Jaccard + time-decay + keyword
   cosine; start with a tuned threshold, then a small logistic over features).
3. **Score the join's ARI/BCubed-F1 vs gold** (offline, free) against the ~0.75 Sonnet
   noise floor and the ~92-F1 literature bar.
4. **Decision gate:** if the deterministic join alone clears ~0.75 ARI / ~90 F1, the
   redesign is validated and we build Stages 0–3 properly. If it lands at the old MiniLM
   ~0.50, the entity+time signal isn't enough on our feeds and we rethink. Either way it's a
   cheap, decisive answer — exactly the "PoC before architecture" rule.

## Key sources

Saravanakumar news-stream (sparse-vs-dense numbers) https://arxiv.org/abs/2101.11059 ·
Miranda/Priberam streaming + learned threshold https://arxiv.org/abs/1809.00540
(code https://github.com/Priberam/news-clustering) · EpiMine episode hierarchy
https://arxiv.org/abs/2408.04873 · Synergetic extract-then-link https://arxiv.org/html/2406.02148v1 ·
Nath distilled rationales https://aclanthology.org/2024.naacl-long.218/ · ACCI SOTA
https://www.nature.com/articles/s41598-025-32765-6 · quasi-identity/granularity
https://www.cs.cmu.edu/~hovy/papers/13HLT-Events-workshop.pdf · Event Registry
https://www.blog.eventregistry.org/harnessing-ai-and-nlp-how-event-registry-transforms-global-news-into-actionable-insights/ ·
NewsCatcher (embeddings→Leiden) https://www.newscatcherapi.com/docs/v3/documentation/guides-and-concepts/clustering-news-articles ·
Feedly LSH dedup https://feedly.com/engineering/posts/reducing-clustering-latency ·
Model2Vec https://www.xugj520.cn/en/archives/model2vec-fast-static-embedding.html ·
Digg-as-AI-aggregator https://techcrunch.com/2026/05/11/digg-tries-again-this-time-as-an-ai-news-aggregator/ ·
Techmeme https://news.techmeme.com/250912/20-years · Google News
https://searchengineland.com/google-news-ranking-stories-30424 · Particle
https://techcrunch.com/2024/11/12/particle-launches-an-ai-news-app-to-help-publishers-instead-of-just-stealing-their-work/
