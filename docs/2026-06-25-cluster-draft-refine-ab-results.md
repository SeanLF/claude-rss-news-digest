# CLUSTER draft-refine A/B — results (2026-06-25)

Measures the Arch-2 cost lever: does feeding the SDK CLUSTER stage a **pre-clustered
draft** (so it refines instead of clustering from scratch) cut its output tokens /
cost, **at equal quality**? CLUSTER is ~38% of per-run cost and its cost is almost
all output tokens (run 205: in 13 tok, out 59,900). Companion to the scoping doc
`2026-06-25-cluster-thinking-config-ab-design.md` (that one tests thinking-budget;
this one tests draft-injection — the settled Arch-2 question).

## TL;DR

**A free local-embedding draft → Sonnet refine is NOT a free win.** A draft does cut
output tokens 51–58% and cost ~34%, but the embedding draft's noise propagates and
quality drops below the from-scratch baseline. Recovering baseline quality (a
two-pass "merge + keep boundaries" prompt) **erases the saving** — it induces more
agentic turns, so cache-read cost rises above baseline. The perfect-draft control
(`draft-gold`) **is** both cheaper *and* better, proving the ceiling is real and the
limiter is draft quality, not the mechanism. **Recommendation: do not ship any draft
variant; keep CLUSTER from-scratch on Sonnet.** The high-quality NIM-draft path
(DeepSeek-V4-Pro batched) that this doc originally proposed was **built and REFUTED
2026-06-26** (see "DeepSeek-batched draft" section): a production-realistic DeepSeek
draft scores ARI 0.671 ≈ the embedding draft, NOT the 0.957 closed-subset number, so
its refine anchors below baseline identically. **The draft-injection lever (Arch-2)
is closed in all tested forms** — `draft-gold` needs a near-perfect draft no
production source can produce. Next lever: thinking-config A/B (separate doc).

## Method

- **Harness** `scratch/cluster-replay/replay_ab.py` runs the *real* CLUSTER stage via
  the Agent SDK inside the `digest-newsroom` container (a subprocess — dodges the
  `CLAUDECODE=1` nested-Claude block), against archived `run_artifacts` (runs
  204–211). No email / DB-write / prod impact. Reuses `cluster_poc.adjusted_rand`
  for ARI and `eval_stages.grade_cluster` for structural checks.
- **Drafts** `scratch/cluster-replay/make_embed_drafts.py` (host, MPS): Qwen3-0.6B
  embeddings, agglomerative @ a **fixed** cosine-distance 0.45 (production can't tune
  per-run against gold). Labels each draft cluster with its lowest-id article title.
- **Scoring** `scratch/cluster-replay/analyze.py`: pairwise same-cluster
  **precision/recall** (not ARI alone) + junk-drawer / under-merge counts, so the
  quality gap is decomposed by error mode.
- **Deflation checks (both passed):** baseline-204 replay reproduced the historical
  cost ($0.590 vs recorded $0.61) and token band; embedding drafts reproduced the
  prior PoC ARIs exactly (204 = 0.667, 205 = 0.463, …).

`cost` below is the SDK `total_cost_usd` (API-equivalent; the pipeline runs on
included subscription usage, so this is the proxy for usage consumed). Output tokens
include thinking tokens when thinking is on; all runs here use thinking **off** (prod
config).

## Variants

| id | draft fed | refine prompt |
|----|-----------|---------------|
| `baseline` | none | production cluster.md, cluster from scratch (control) |
| `draft-gold` | run's own gold clusters.json | "refine this draft" — the optimistic **ceiling** |
| `draft-embed` | Qwen3-0.6B @0.45 (ARI ~0.69) | "trust the draft, fix errors" |
| `draft-embed-merge` | same | "the draft over-splits — merge aggressively" |
| `draft-embed-balanced` | same | "merge the scatter AND keep sub-story boundaries" |

## Results — run 204 (241 articles, 160 gold clusters)

| variant | out tok | cache-read | cost | ARI | precision | recall | F1 | junk |
|---------|--------:|-----------:|-----:|----:|----------:|-------:|---:|-----:|
| baseline             | 20,484 | 103k | $0.590 | 0.752 | 0.817 | 0.698 | 0.753 | 1 |
| draft-gold (ceiling) | 10,050 |  61k | **$0.410** | 0.838 | 0.904 | 0.788 | **0.842** | 1 |
| draft-embed (trust)  |  8,559 |  65k | $0.383 | 0.661 | 0.621 | 0.715 | 0.665 | 5 |
| draft-embed-merge    |  8,484 |  64k | $0.388 | 0.703 | 0.699 | 0.715 | 0.707 | 3 |
| draft-embed-balanced | 14,875 | 186k | $0.638 | 0.755 | 0.894 | 0.659 | 0.759 | 1 |

Baseline-vs-baseline note: a from-scratch Sonnet re-run scores **ARI 0.75 / F1 0.75
vs the archived gold**, not 1.0 — that is Sonnet's own run-to-run reproducibility and
the true "equal quality" bar (the scoping doc's "ARI vs fixed baseline" omitted this).

## Findings

1. **The mechanism cuts tokens/cost.** Trust/merge drafts cut output tokens 51–58%
   and cost ~34% vs baseline (single-pass: the model trusts the draft and finishes in
   fewer agentic turns → fewer cache-reads *and* less output).

2. **But not at equal quality, and the failure mode is the OPPOSITE of the obvious
   guess.** ARI alone says "draft variants are worse" and would push you to *merge
   more*. The P/R decomposition shows why that's wrong: the draft variants' recall
   (0.715) already **beats** baseline (0.698); their gap is **precision** (0.62–0.70
   vs 0.82) — Sonnet over-merges the draft's scattered pieces into **junk-drawer
   clusters** (5 and 3, vs baseline's 1), lumping distinct facets of one event
   ("congressional vote" + "diplomatic reaction" + "analysis") into mega-clusters.
   This is exactly the "ARI understates quality / audit by hand" lesson — the right
   fix is an anti-junk *precision* instruction, not "merge more."

3. **Recovering baseline quality erases the saving.** The `balanced` prompt (merge
   *and* hold sub-story boundaries) reaches baseline F1 (0.759) and kills the
   junk-drawers (1) — but its two-pass reasoning makes the model re-read the article
   context more, so **cache-read jumps to 186k and cost ($0.638) EXCEEDS baseline
   ($0.590)** despite fewer output tokens. Key structural insight: **CLUSTER cost ≈
   f(agentic turns × context cache-reads + output)** — not output tokens alone. A
   prompt that buys quality with extra turns is a net loss.

4. **The ceiling is real — the limiter is draft quality.** `draft-gold` (a
   near-perfect draft) is simultaneously **cheaper** ($0.410 vs $0.590) **and better**
   (F1 0.842 vs 0.753). So when the draft is good, refine genuinely wins on both axes.
   The free embedding draft (ARI ~0.69) is just too noisy: Sonnet either trusts it
   (inherits its errors → low precision) or fully re-examines it (no saving).

## Conclusion & recommendation

- **No realistic-embedding-draft variant dominates baseline** (cheaper *and*
  equal-quality). It is a quality/cost trade with no free point.
- **Don't ship the free-embedding Arch-2.** Keep CLUSTER from-scratch on Sonnet.
- ~~**The viable path is a high-quality draft.** A DeepSeek-batched draft → Sonnet
  refine should approach the `draft-gold` operating point.~~ **TESTED 2026-06-26 and
  REFUTED** — see "DeepSeek-batched draft" section below. The 0.957/chunk was a
  closed-subset artifact; a *production-realistic* DeepSeek-batched draft scores ARI
  **0.671** (≈ the 0.69 embedding draft, NOT 0.957), because real chunking can't
  co-locate every story's articles in one context without peeking at gold. The refine
  anchors to it (F1 0.70 < baseline 0.75) just like the embedding draft. **No draft
  source that exists in production reaches the `draft-gold` operating point — the
  CLUSTER-draft lever is closed.**
- **A cheaper-but-lower-quality option exists if wanted:** the trust/merge prompt is
  a ~34% CLUSTER cost cut (~13% of total pipeline) at the price of ~2 extra
  junk-drawer clusters/digest (F1 0.71 vs 0.75). A product call for a quality-first
  digest — not a clear win.

## Reproduce

```bash
# host: generate embedding drafts (uv pulls torch/sentence-transformers; HF cached)
HF_HUB_OFFLINE=1 uv run --no-project --with sentence-transformers --with scikit-learn \
  --with torch python scratch/cluster-replay/make_embed_drafts.py qwen06 0.45 204 205

# docker: replay a variant (SDK, no CLAUDECODE block)
docker compose run --rm -v "$PWD/scratch:/app/scratch" digest-newsroom \
  .venv/bin/python /app/scratch/cluster-replay/replay_ab.py --run 204 --variant draft-embed-merge

# host: offline P/R audit
python3 scratch/cluster-replay/analyze.py 204 baseline-off draft-embed-merge-off draft-embed-balanced-off
```

## Large-run confirmation — run 205 (465 articles, 225 gold; draft ARI **0.463**, the worst case)

| variant | out tok | cost | ARI | precision | recall | F1 | junk |
|---------|--------:|-----:|----:|----------:|-------:|---:|-----:|
| baseline            | 33,456 | $1.077 | 0.746 | 0.817 | 0.703 | 0.756 | 2 |
| draft-embed (trust) | 28,863 | $1.094 | 0.523 | 0.684 | 0.427 | 0.525 | 3 |
| draft-embed-merge   | 22,806 | $0.901 | 0.519 | 0.644 | 0.483 | 0.552 | 4 |

(Note: draft-embed(trust) on 205 *cost as much as baseline* — its heavy under-merge
left the model thrashing; the saving only appears in the merge variant.)

**Confirms anchoring at scale and a quality-dependent error profile:**

- Baseline reproduces the **same ~0.75 noise floor** as run 204 (0.746 vs 0.752) —
  Sonnet's from-scratch reproducibility is stable across runs. This is the real
  "equal quality" bar.
- With a **bad** draft (0.463), refine output anchors near it: F1 0.52–0.55, a **0.20+
  drop** below baseline (0.756), for only ~32% output-token / ~16% cost saving. Bad
  trade.
- **The error mode flips with draft quality.** Good draft (204): refine over-merges →
  *precision* loss (junk-drawers). Bad draft (205): refine can't undo the heavy
  over-split → *recall* collapses (0.43, under-merge). Either way the output tracks
  the draft, not the baseline.

Net across both runs: **the free local-embedding draft is the binding constraint** —
refine inherits its quality. The 210 pair was cut to respect the 14.5% weekly budget
cap; 204 (good draft) + 205 (worst draft) already bracket the behaviour.

## DeepSeek-batched draft — the premium-draft path, TESTED (2026-06-26)

The recommendation above said the version worth building is a HIGH-quality NIM draft
(DeepSeek-V4-Pro, 0.957/chunk per `project_nim_models`) → Sonnet refine, expected to
reach the `draft-gold` operating point. **Built and tested it. It does not work — and
the reason kills the whole premium-draft idea, not just this implementation.**

**Why the 0.957/chunk number doesn't carry to production.** That figure was measured
on a CLOSED SUBSET (`cluster_poc.py`): ~120 articles hand-picked as 15 whole baseline
clusters, so every article's story-mates were guaranteed co-present in one context.
Production CLUSTER sees ~240–465 articles forming ~160–225 clusters (mostly
singletons + a few big cross-cutting stories), and every NIM model — DeepSeek-V4-Pro
included, despite its 1M context — **breaks one-shot at ~460 articles** (output-side
coherence collapses to ~46% coverage; see `project_nim_models`). So a real draft MUST
batch into ≤~120-article chunks — and then it can only group articles that happen to
share a chunk. You cannot put every story's articles in the same chunk without already
knowing the clustering (circular) or using a pre-grouping — and the best free
pre-grouping is the 0.69 embedding draft, which becomes the ceiling.

**Harness** (`make_deepseek_drafts.py`, host, stdlib + Olla/NIM): pack articles into
≤110-article chunks from the *existing embedding draft's* groupings (keeps
embedding-related articles together so cross-chunk story splits are rarer than random
packing), cluster each chunk with DeepSeek-V4-Pro via Olla, then one DeepSeek
reconcile pass to merge cross-chunk fragments. Writes `drafts/draft_deepseek_<run>.json`
in the same schema the replay's refine variants consume. A reliability bug found and
fixed mid-run: the free tier returns **empty 200 streams** during 429 bursts, which the
old `call_olla` returned as `""` and the caller silently exploded into singletons
(run 204 chunk 1 first attempt: out_tok=0 → 110 bogus singletons, corrupting the draft
to ARI 0.702). `call_olla` now retries empty/garbled completions; re-run was clean.

**Run 204 draft quality (the cheap offline gate, BEFORE any Sonnet spend):** 241
articles → 3 chunks (110/110/21), each clustered cleanly (8–13 tok/s, served
deepseek-v4-pro). Reconcile got 429-throttled and was skipped. **Draft ARI vs gold =
0.671** — essentially the embedding draft (0.688), NOT 0.957. Structural audit: the
draft matches gold's singleton count exactly (125 = 125) and nails cohesive stories
(Russian warship 6 arts → 1 cluster, Bolsonaro 5 → 1), but shatters the big
cross-cutting ones (US-Iran peace deal 11 arts → 5 clusters, oil 5 → 4, G7 5 → 3) —
chunk boundaries plus DeepSeek's rubric splitting facets gold lumps.

**Run 204 refine (real Sonnet via SDK, thinking off):**

| variant | out tok | cost | ARI | precision | recall | F1 | junk |
|---------|--------:|-----:|----:|----------:|-------:|---:|-----:|
| baseline                 | 20,484 | $0.590 | 0.752 | 0.817 | 0.698 | 0.753 | 1 |
| draft-gold (ceiling)     | 10,050 | $0.410 | 0.838 | 0.904 | 0.788 | 0.842 | 1 |
| draft-embed (trust)      |  8,559 | $0.383 | 0.661 | 0.621 | 0.715 | 0.665 | 5 |
| **draft-deepseek (trust)**     |  8,705 | $0.387 | 0.696 | 0.836 | 0.598 | 0.697 | 1 |
| **draft-deepseek (merge)**     | 13,443 | $0.514 | 0.699 | 0.755 | 0.654 | 0.701 | 4 |

**Findings:**

1. **The DeepSeek draft refine ≈ the embedding draft refine.** Trust-refine: same
   ~34% cost cut ($0.387 vs baseline $0.590), same sub-baseline F1 (0.697 vs 0.665).
   The refine **output ARI (0.696) tracks the draft's own ARI (0.671)** — anchoring,
   identical to the embedding result. A 0.67 draft buys a 0.70 refine regardless of
   the draft's provenance.

2. **DeepSeek's error mode is the MIRROR of embeddings' — and it nets the same F1.**
   Embedding draft → Sonnet OVER-merges into junk-drawers (precision 0.621, junk 5).
   DeepSeek draft → Sonnet UNDER-merges, keeping the draft's facet-splits (recall
   0.598) but with baseline-clean precision (0.836, vs baseline 0.817, junk 1).
   Opposite failure, same F1 ≈ 0.70. Whichever way the draft is wrong, the refine
   inherits a ~0.05–0.06 F1 deficit vs from-scratch.

3. **The merge prompt does NOT rescue it.** The obvious fix for an under-merged draft
   is the MERGE refine prompt (built for the embedding draft's over-split). On the
   DeepSeek draft it hit gold's cluster count exactly (160) but **F1 stayed flat
   (0.701)**: it traded precision (0.836 → 0.755) for recall (0.598 → 0.654) and
   created junk-drawers (1 → 4) — merging to the right *count* along the wrong
   *lines*. Cost rose to $0.514 (more agentic turns). Prompt framing moves the error
   mode; it does not let Sonnet reconstruct baseline quality from a 0.67 draft.

4. **`draft-gold` stays the only both-cheaper-and-better point** — and it needs a
   near-perfect draft (ARI ≳0.95) that no production-runnable source produces. Free
   embeddings give 0.69; batched DeepSeek-V4-Pro gives 0.67. The gap to 0.95 is the
   cross-chunk co-location problem, which is unsolvable without the answer.

**Conclusion:** the premium-draft path is **refuted by direct test**. A
production-realistic high-quality NIM draft is not actually high-quality (0.67, not
0.957), so the refine anchors below baseline exactly like the free embedding draft —
at which point you've spent NIM tokens + Sonnet tokens to get *worse* clustering than
from-scratch. **Keep CLUSTER from-scratch on Sonnet. The draft-injection lever
(Arch-2) is closed in all tested forms.** The remaining untested CLUSTER cost lever is
the thinking-config A/B (`2026-06-25-cluster-thinking-config-ab-design.md`), now to be
measured on `total_cost_usd` given the cache-read-dominated cost structure.

**Caveats / not-done (NIM 429-throttled at test time, ~11:30 EDT 2026-06-26):**
- The closed-subset 0.957 repro (the clean deflation check that my harness reproduces
  the published number) is **pending off-peak** — NIM returned sustained 429s through
  10 backed-off retries. The artifact argument rests instead on the direct evidence:
  refine output ARI tracks draft ARI, and the structural audit shows the fragmentation
  is real and interpretable, not a parse/coverage bug.
- Run 205 (the worst-draft bracket) DeepSeek point was **not generated** (same
  throttle). Run 204 + the anchoring mechanism are decisive; a 205 confirmation
  off-peak would only add a second point on the same line.

## Reproduce (DeepSeek path)

```bash
# host: Olla up with a NIM key (ccnim, or start the daemon directly), then:
python3 scratch/cluster-replay/make_deepseek_drafts.py 204        # writes drafts/draft_deepseek_204.json + prints draft ARI
# docker: Sonnet refine on the DeepSeek draft (trust + merge prompts)
docker compose run --rm -v "$PWD/scratch:/app/scratch" digest-newsroom \
  .venv/bin/python /app/scratch/cluster-replay/replay_ab.py --run 204 --variant draft-deepseek
docker compose run --rm -v "$PWD/scratch:/app/scratch" digest-newsroom \
  .venv/bin/python /app/scratch/cluster-replay/replay_ab.py --run 204 --variant draft-deepseek-merge
# host: offline P/R audit
python3 scratch/cluster-replay/analyze.py 204 baseline-off draft-deepseek-off draft-deepseek-merge-off
```
