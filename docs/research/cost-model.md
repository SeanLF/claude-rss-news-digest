# News Digest: Cost Model

**Data basis:** 18 production runs (2026-03-17 to 2026-04-03), all Sonnet 4.x  
**Actual spend:** $0 (Claude subscription). Figures below are API-equivalent costs for planning.  
**Pricing used:** Sonnet at input $3/M, output $15/M, cache-write $3.75/M, cache-read $0.30/M

---

## 1. What a run costs today

### Observed range

| Metric | Value |
|--------|-------|
| Mean per-run cost | $4.97 |
| Min per-run cost | $2.55 (run 118, 437 articles) |
| Max per-run cost | $8.23 (run 116, 682 articles) |
| Typical daily range | $3 - $6 |
| Article volume | 437 - 704 (avg ~617) |

One run (run 124) shows anomalous subagent label misidentification (recap recorded with 1.8M cache-read tokens and $2.09 cost; actual recap cost is ~$0.07). The mean above excludes this outlier.

### Cost breakdown by subagent

These are averages across 18 runs, normalized by subagent role:

| Subagent | Model | Avg cost/run | % of total | Avg cache-read tokens |
|----------|-------|-------------|------------|----------------------|
| CLUSTER | Sonnet | $2.54 | 51% | 4,304,041 |
| FACT-CHECK (coherence) | Sonnet | $0.70 | 14% | 344,324 |
| DISPATCHER | Sonnet | $0.54 | 11% | 364,173 |
| SELECT | Sonnet | $0.50 | 10% | 354,104 |
| WRITE | Sonnet | $0.50 | 10% | 324,577 |
| RECAP/SUMMARIZE | Sonnet | $0.07 | 1% | 47,834 |

CLUSTER dominates at ~51% of API-equivalent spend. The memory note of "63% / $6.74/day" was from the prior Haiku-priced estimate; with updated Sonnet pricing across all agents and a wider data window, cluster is still the dominant cost but at ~51%.

---

## 2. What drives cost

### Primary driver: CLUSTER's cache-read volume

CLUSTER is expensive because it reads all ~600 articles across multiple CSV files in a multi-turn conversation. Each article file gets cached; subsequent turns re-read from cache. The cache-read token count varies wildly (29k to 12.4M across runs), and cache-read tokens at $0.30/M are still a real cost at that volume.

Key insight from the data: cache-read volume per article is unpredictable. Two runs with similar article counts (661 vs 677 articles) produced cluster costs of $0.09 and $3.66 respectively. The difference is how many conversation turns the subagent needed -- more turns = more re-reads of the cached article corpus. The article count alone is not a reliable cost predictor.

**What actually drives cluster cost:**
1. Number of conversation turns (more article files to read = more turns)
2. Whether the agent self-corrects or retries (each retry re-reads everything)
3. Article volume (more articles = more initial cache-write tokens, larger context)

### Secondary drivers

**Output token volume** affects WRITE and FACT-CHECK the most. The digest has a relatively fixed output size (3-6 must_know, 5-8 should_know, 20-30 signals), so output tokens are mostly stable regardless of input article count.

**Cache efficiency.** All subagents operate at high cache-hit rates:

| Subagent | Cache-read % of total tokens |
|----------|------------------------------|
| CLUSTER | 97.1% |
| RECAP | 91.2% |
| DISPATCHER | 86.3% |
| SELECT | 84.0% |
| WRITE | 83.4% |
| FACT-CHECK | 79.3% |

These rates reflect Claude Code's prompt caching. The system prompt + article data is written once per session turn and re-read cheaply on subsequent turns. This is already well-optimized. The only lever here would be reducing the number of turns CLUSTER needs.

### What does NOT drive cost much

- **Number of sources (35):** Sources are listed in a small CSV. Adding 10 more sources has negligible direct cost impact; only matters if it increases fetched article volume.
- **Output length of the digest:** The final HTML render is done in Python, not by Claude. Claude produces a structured JSON of fixed maximum size.
- **RECAP/COHERENCE complexity:** These are cheap (<$0.10 each) and stable.

---

## 3. Scaling with frequency

Since cost is roughly fixed per run (not per subscriber, not per day's news volume -- within the current range):

| Frequency | Annual API-equivalent cost |
|-----------|---------------------------|
| Daily (365 runs) | $1,815 |
| Every 2 days (182 runs) | $905 |
| Weekdays only (260 runs) | $1,292 |
| Weekly (52 runs) | $258 |
| Monthly (12 runs) | $60 |

Note: weekly or monthly runs may face higher CLUSTER costs because more articles have accumulated since the last run (the dedup window is 7 days, but article volume per run would grow if running less frequently). This is untested.

---

## 4. Scaling with article volume

The relationship between article count and cost is noisy (see Section 2), but there is a general directional trend in CLUSTER:

| Articles | Estimated CLUSTER cost | Total estimated cost |
|----------|----------------------|---------------------|
| 100 | ~$0.30 | ~$1.20 |
| 300 | ~$0.80 | ~$2.10 |
| 500 (typical) | ~$1.80 | ~$3.50 |
| 700 (high) | ~$3.00 | ~$5.00 |
| 1000 | ~$4.50+ | ~$7.00+ |
| 2000 | unknown, likely non-linear | $12-20+ |

These are rough estimates. At 2000 articles, CLUSTER would likely need more conversation turns, hitting a compounding cache-read cost (each turn re-reads the full corpus). The current `MAX_TOKENS_PER_FILE = 10000` token limit splits articles across multiple files; 2000 articles would produce ~20+ files, and CLUSTER is already instructed to read them all.

**Assumption:** The model's context window (200k tokens for Sonnet) is not a hard ceiling at current volumes. At 700 articles with ~200 tokens/article average (title + summary), that's ~140k tokens of article content -- within limits but already large.

---

## 5. Cache efficiency details

The system is already well-optimized for cache usage. Understanding what is and isn't cached:

**What gets cached (within a run):**
- The article corpus (articles_*.csv files) -- written once per CLUSTER turn, re-read cheaply on subsequent turns
- Agent system prompts -- cached across the subagent's multi-turn conversation
- Intermediate files (clusters.json, selected.json) -- written once, read by downstream agents

**What does NOT benefit from cross-run caching:**
- Articles change every day -- yesterday's cache is useless
- Digest output format changes occasionally -- prompts change with it
- The CLUSTER prompt explicitly changes each run (different article IDs)

**Implication:** There is no "warm cache" from previous days. Every run starts cold. The cache-read tokens visible in `run_usage` are all within-session (intra-run) caching, not cross-day.

**Improving cache efficiency:** The main opportunity is reducing CLUSTER's conversation turns. If CLUSTER could read all articles in a single pass (one tool-use per file rather than interactive turns), cache-read volume would drop significantly. This is an architecture change, not a prompt change.

---

## 6. Break-even analysis

**Assumption:** The operator pays API pricing (not a Claude subscription). This is the scenario where shared digest economics matter.

**Current cost baseline:** $5/day (median), $1,825/year.

### At $5/month per subscriber

| Subscribers | Monthly revenue | Monthly cost | Monthly profit |
|-------------|----------------|--------------|----------------|
| 10 | $50 | $152 | -$102 |
| 30 | $150 | $152 | -$2 |
| 31 | $155 | $152 | +$3 |
| 50 | $250 | $152 | +$98 |
| 100 | $500 | $152 | +$348 |

**Break-even: ~31 subscribers**

### At $10/month per subscriber

| Subscribers | Monthly revenue | Monthly cost | Monthly profit |
|-------------|----------------|--------------|----------------|
| 10 | $100 | $152 | -$52 |
| 16 | $160 | $152 | +$8 |
| 50 | $500 | $152 | +$348 |

**Break-even: ~16 subscribers**

### At $20/month per subscriber

| Subscribers | Monthly revenue | Monthly cost | Monthly profit |
|-------------|----------------|--------------|----------------|
| 8 | $160 | $152 | +$8 |
| 10 | $200 | $152 | +$48 |

**Break-even: ~8 subscribers**

Monthly cost figure ($152) = $5/day x 30.4 days. Does not include infrastructure (Hetzner CX23 is ~€4/month), email sending (Resend), or labour.

**Key structural advantage:** Cost is per run, not per subscriber. The 10th subscriber costs exactly the same as the 1st. Once above break-even, incremental margin is 100% of subscription revenue minus email sending costs (Resend free tier covers up to 3,000 emails/month; after that ~$0.001/email).

---

## 7. Key uncertainties and risks

### CLUSTER cost variance is the main risk

The 10x variance in cluster cost ($0.09 to $5.54 in the data, with one outlier at $0.09 likely being a failure/minimal run) means monthly cost swings of ~$50-100 are possible even with stable article volume. Budget for the high end.

### Subagent retry cost

The dispatcher retries failed agents once. A single CLUSTER retry doubles that subagent's cost for the run. There is no retry counter in `run_usage`, so the current data already includes some retry costs silently.

### Model pricing changes

All pricing is as of 2026-03-19 (Sonnet 4.x). Anthropic has historically moved prices downward over model generations. A 2x price reduction (plausible by end of 2026) would halve break-even subscriber counts.

### Subscription vs API

The current setup runs on a Claude subscription ($0 marginal API cost). Moving to API billing would require passing $5/day in real cash, not accounting equivalents. The break-even analysis above models that scenario.

---

## 8. Optimization opportunities (ranked by expected impact)

1. **Reduce CLUSTER conversation turns** (high impact, medium effort): Restructure CLUSTER to read all article files in a single assistant turn rather than iteratively. Estimated cost reduction: 30-50% of CLUSTER cost, or $0.75-1.25/run.

2. **Weekly digest instead of daily** (high impact, scope change): Reduces runs from 365 to 52/year. Annual cost drops from ~$1,825 to ~$260. Trade-off: stale news, different product.

3. **Reduce article volume via smarter pre-filtering** (medium impact, medium effort): TF-IDF dedup already runs pre-Claude. Tightening the threshold or adding URL-level dedup could reduce articles from 600 to 400. Estimated cluster cost reduction: 20-30%.

4. **Cheaper model for CLUSTER** (medium impact, low effort if quality holds): Haiku at $0.10/M input vs Sonnet at $3/M input would dramatically cut cluster cost -- if clustering quality is acceptable. The 2026-03 PoC found no model matched Claude's editorial judgment for narrative grouping, but Haiku was not specifically tested for mechanical clustering (same event, not same theme).

5. **Cap article file reads** (low impact, low effort): CLUSTER is instructed to read ALL articles_*.csv files. A hard cap (e.g., top-N articles by recency/source priority before sending to Claude) would reduce input size predictably.

---

## 9. Data quality notes

- 18 runs tracked with `run_usage` data (2026-03-17 to 2026-04-03)
- All runs used Sonnet 4.x (`claude-sonnet-4-6`)
- Run 124 has a subagent label mismatch (recap recorded as doing SELECT/WRITE-scale work); treat its per-subagent breakdown as unreliable, though the run total is accurate
- The "summarize" label in older runs is the predecessor to "recap" -- same role, renamed
- No Haiku runs are present in `run_usage` data despite Haiku being listed as used for RECAP and COHERENCE in project memory; the actual runs show Sonnet for all agents. Either the model selection changed or the memory note is stale.
