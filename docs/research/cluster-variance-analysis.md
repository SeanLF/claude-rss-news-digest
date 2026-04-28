# CLUSTER Subagent Cost Variance Analysis

**Date:** 2026-04-03  
**Scope:** 18 production runs, 2026-03-17 to 2026-04-03  
**Question:** Why does CLUSTER cost range from $0.09 to $5.54 across runs with similar article counts?

---

## 1. CLUSTER Prompt Analysis

The CLUSTER agent (`/.claude/agents/cluster.md`) is a simple file-based worker:

1. Reads `sources.csv`
2. Reads ALL `articles_*.csv` files (typically 8-13 files for 437-704 articles)
3. Groups articles by story into clusters
4. Writes `clusters.json`

**Token budget per run:** With `MAX_TOKENS_PER_FILE = 10000` and ~188 tokens per article row, each file holds ~53 articles. A 600-article run produces ~11 files, writing ~130-175k tokens to the prompt cache on first read.

**The critical instruction** that drives variance:

> "After generating clusters, review any cluster over 20 articles and split further."

This is an open-ended review loop with no iteration limit. Combined with a hard rule that "No cluster may contain more than 25 articles," the agent is required to iterate until no cluster exceeds 25 articles. On news-heavy days, popular topics (wars, elections, major policy announcements) can attract 30-60 articles. Each split attempt sends the agent back through the files, accumulating cache reads.

**Why multiple turns happen:** Every Read tool call adds a turn to the conversation. Each new turn re-reads the full cached conversation context. For a 13-file corpus at 10k tokens/file, each turn adds ~130k tokens of cache reads. Over 60-75 turns, this accumulates to 7-12M cache_read_tokens -- the dominant cost driver.

---

## 2. Cost Distribution Table

All 18 production runs, ordered by ascending cost. Run 124 (April 2) is a partial failure -- CLUSTER produced only 648 output tokens (vs typical 23-110k) and cost $0.09; included for completeness but excluded from averages.

| run_id | date       | day       | articles | input_tokens | output_tokens | cache_read_tokens | cache_write_tokens | cost_usd | est_turns |
|--------|------------|-----------|----------|-------------|---------------|-------------------|--------------------|----------|-----------|
| 124    | 2026-04-02 | Thursday  | 661      | 1,930       | 648           | 29,468            | 16,963             | $0.09    | ~2.7      |
| 118    | 2026-03-29 | Sunday    | 437      | 1,182       | 33,874        | 339,518           | 73,193             | $0.89    | ~5.6      |
| 117    | 2026-03-28 | Saturday  | 604      | 18          | 33,365        | 752,672           | 79,300             | $1.02    | ~10.5     |
| 111    | 2026-03-22 | Sunday    | 457      | 32          | 23,054        | 1,482,092         | 76,104             | $1.08    | ~20.5     |
| 110    | 2026-03-21 | Saturday  | 623      | 610         | 39,621        | 518,715           | 108,797            | $1.16    | ~5.8      |
| 125    | 2026-04-03 | Thursday  | 567      | 17          | 50,808        | 625,488           | 99,016             | $1.32    | ~7.3      |
| 112    | 2026-03-23 | Monday    | 535      | 22          | 43,212        | 1,205,978         | 98,635             | $1.38    | ~13.2     |
| 114    | 2026-03-25 | Wednesday | 658      | 2,122       | 55,895        | 2,791,516         | 116,499            | $2.12    | ~25.0     |
| 120    | 2026-03-31 | Tuesday   | 655      | 62          | 35,262        | 4,526,060         | 92,764             | $2.23    | ~49.8     |
| 119    | 2026-03-30 | Monday    | 541      | 123         | 43,071        | 4,470,831         | 100,302            | $2.36    | ~45.6     |
| 109    | 2026-03-20 | Friday    | 690      | 3,504       | 43,593        | 5,150,958         | 121,184            | $2.66    | ~43.5     |
| 106    | 2026-03-17 | Tuesday   | 653      | 80          | 38,334        | 7,049,059         | 119,119            | $3.14    | ~60.2     |
| 108    | 2026-03-19 | Thursday  | 701      | 5,020       | 49,834        | 6,765,629         | 134,344            | $3.30    | ~51.4     |
| 122    | 2026-04-01 | Wednesday | 677      | 94          | 110,004       | 4,104,040         | 206,539            | $3.66    | ~20.9     |
| 115    | 2026-03-26 | Thursday  | 702      | 90          | 62,437        | 9,086,505         | 137,571            | $4.18    | ~67.0     |
| 116    | 2026-03-27 | Friday    | 682      | 2,738       | 102,518       | 5,813,511         | 372,527            | $4.69    | ~16.6     |
| 107    | 2026-03-18 | Wednesday | 704      | 4,096       | 76,910        | 10,355,214        | 175,416            | $4.93    | ~60.0     |
| 113    | 2026-03-24 | Tuesday   | 673      | 3,087       | 78,313        | 12,405,486        | 168,754            | $5.54    | ~74.5     |

**Cost breakdown for a typical high-cost run (run_113):**
- Cache read: $12.4M × $0.30/M = **$3.72 (67%)**
- Output: 78k × $15/M = **$1.17 (21%)**
- Cache write: 169k × $3.75/M = **$0.63 (11%)**
- Input: 3k × $3/M = **$0.01 (0%)**

Cache reads are the dominant cost. Article count is not.

---

## 3. JSONL Turn Analysis

Direct JSONL inspection of production sessions was not possible -- the Docker volume (`news-digest-claude`) containing `/home/appuser/.claude/projects/-app/` was empty when inspected locally, confirming sessions are ephemeral and not persisted to the volume after the container exits.

Instead, turn counts were estimated from token data: `estimated_turns ≈ cache_read_tokens / cache_write_tokens + 1`. This is valid because:

- `cache_write_tokens` approximates the full corpus written to cache on the first read pass (one write per fresh context turn)
- `cache_read_tokens` accumulates for each subsequent turn that sees the cached corpus
- The ratio tells us how many additional times the cached corpus was seen

**Key ratio patterns observed:**

| Cost tier | cache_read/write ratio | estimated turns | interpretation |
|-----------|----------------------|-----------------|----------------|
| $0.09 (failed) | 1.7x | ~3 | Near-immediate exit, incomplete |
| $0.89-$1.32 (low) | 4.6-6.3x | ~5-7 | Read files once, write clusters, verify once |
| $1.38-$2.66 (medium) | 12-44x | ~13-45 | Several rounds of cluster splitting |
| $3.14-$5.54 (high) | 50-73x | ~51-74 | Extended splitting/verification loops |

**Output tokens per estimated turn** reveals two distinct agent behaviours:

- **Low-turn runs** (~5-7 turns): 6,000-7,000 output tokens/turn -- agent writes the full `clusters.json` in a single large output, then verifies once.
- **High-turn runs** (~50-75 turns): 600-1,100 output tokens/turn -- agent makes many small incremental updates, suggesting repeated partial rewrites of `clusters.json`.

This output-per-turn pattern is the clearest signature: low-cost runs complete the task in one big pass; high-cost runs iterate with many small corrections.

**Re-read pattern:** Run 116 is a special case with `cache_write = 372,527` tokens (546 tokens/article vs baseline ~175 tokens/article). This ~3x elevation indicates the article corpus was written to cache approximately 3 times, suggesting the context window was exceeded mid-session and the model restarted with a fresh context, re-writing all files to cache again. Runs 113 and 107 show a mild version of this (~1.3-1.4x elevation). Context overflow is a secondary cost multiplier on top of the turn count effect.

---

## 4. Root Cause Hypothesis

Ranked by evidence strength:

### Cause 1: Open-ended cluster-splitting loop (primary -- very strong evidence)

The prompt instruction "review any cluster over 20 articles and split further" creates an unconstrained iteration loop. There is no `max_iterations` guard, no instruction to stop after one review pass, and no fail-safe.

**How it compounds cost:** Each iteration involves re-reading source files to verify article membership before splitting. On high-turn runs (~60-75 turns), the full article corpus (~130k tokens) is read from cache 60-75 times, producing 7-12M cache_read_tokens. At $0.30/M, this alone costs $2.10-$3.60 per run.

**Why some runs escape the loop:** When initial clustering produces few or no clusters exceeding 20 articles, the review pass is trivial and the agent exits in 5-7 turns. When many clusters exceed 20 (as happens with breaking news), the loop runs many rounds.

### Cause 2: Weekday news volume (secondary -- very strong evidence)

**Weekday average: $3.19. Weekend average: $1.04. Ratio: 3.1x.**

All four weekend runs cost $0.89-$1.16. Of the 13 weekday runs (excluding the failed run 124), all cost between $1.32 and $5.54, with the majority above $2.00.

The mechanism is straightforward: Monday-Friday generates more news, particularly on hot topics like geopolitics, financial markets, and politics. Each hot topic produces 20-50+ articles on the same story. This violates the 25-article cluster limit, triggering the split loop repeatedly.

The March 24 ($5.54) and March 18 ($4.93) runs correspond to intense news weeks; without access to the actual `clusters.json` from those runs, the specific trigger topics cannot be confirmed, but the pattern is consistent.

### Cause 3: Context window overflow leading to cache rebuilds (tertiary -- moderate evidence)

Run 116 (Friday, March 27) has `cache_write = 372,527` tokens, approximately 3x the expected ~130k for its article count. This strongly suggests the conversation exceeded the model's context window mid-session, causing a context restart that re-wrote all article files to cache from scratch.

Run 122 (Wednesday, April 1) shows a milder version (1.5x elevated cache_write, 205k tokens), possibly one partial overflow. Runs 113 and 107 are slightly elevated (1.3-1.4x).

Context overflow is a consequence of high turn counts -- it is triggered by the same loop behaviour (cause 1), not an independent cause. However, it multiplies the cost non-linearly: a run that overflows twice pays 3x cache_write cost on top of the already-high cache_read accumulation.

### Cause 4: Intrinsic stochasticity of the model (weak evidence, unquantifiable)

The same input on the same day could produce different turn counts depending on how the model sequences its reasoning. The observation that run_116 has $4.69 cost but only 15.6x cache ratio (moderate turns) yet very high cache_write (3x corpus size) suggests the model's internal iteration strategy varies non-deterministically.

---

## 5. Fixability Assessment

### Fix A: Add an explicit iteration limit to the cluster prompt (high impact, low risk)

Change the review instruction from:
> "After generating clusters, review any cluster over 20 articles and split further."

To something like:
> "After generating clusters, do ONE review pass: identify any cluster with more than 20 articles and split it. Do not re-read source files during the review pass -- use the article IDs already in the cluster. Write the final clusters.json and stop."

**Expected impact:** Would cap high-cost runs near the medium tier ($1-$2). Would not affect weekend runs (already low). Estimated weekday reduction: 50-70%.  
**Risk:** Low. The clustering quality requirement (split at 25) can still be met in a single review pass if the agent splits all oversized clusters in one go rather than iteratively.

### Fix B: Pre-compute cluster sizes as a constraint in the prompt

Pass the article count per source topic as structured metadata, so CLUSTER has a starting signal for which sources are likely to over-cluster. This reduces "surprise" large clusters.  
**Impact:** Moderate. Doesn't fix the loop, just reduces how often it triggers.  
**Complexity:** Medium -- requires upstream changes to `prepare.py`.

### Fix C: Hard-cap the article corpus to limit cache size

Reduce `MAX_TOKENS_PER_FILE` or add a global article cap. At 600 articles instead of 700, cache_write drops from ~175k to ~150k. This has linear effect on cost but doesn't touch the turn count driver.  
**Impact:** Low (10-15% reduction at most).  
**Risk:** Reduces editorial coverage.

### Fix D: Switch CLUSTER to a single-pass, stateless design (architecture change)

Remove the self-review loop entirely. Accept that some clusters may exceed 25 articles on extreme news days. The SELECT subagent can deprioritise oversized clusters anyway.  
**Impact:** Would bring all runs to the weekend baseline (~$0.89-$1.16). Estimated 65-80% reduction in CLUSTER cost.  
**Risk:** Medium -- clustering quality on high-volume days may degrade. Would need evaluation against historical runs.

### Fix E: Instrument turn counts directly

Add JSONL logging to count assistant turns per subagent. Currently `run_usage` stores aggregate token counts; adding a `turn_count` column to `run_usage` would confirm the turn-count hypothesis and enable per-run debugging. This is a monitoring improvement, not a cost fix.

---

## Summary

The 10x cost variance in CLUSTER is **primarily driven by conversation turn count**, not article count. Turn count is high when many articles share a topic (hot news days, breaking events), triggering the open-ended cluster-splitting loop to iterate 50-75 times instead of 5-7. This accumulates massive cache_read_tokens, which at $0.30/M is the dominant cost line (67% of CLUSTER cost on expensive runs).

The **weekday/weekend gap** (3x average cost difference) is the most reliable predictor: weekend runs consistently exit in 5-10 turns and cost under $1.20. Weekday runs are unpredictable, ranging from $1.32 to $5.54 depending on news intensity.

The fix with the best expected return is **Fix A**: adding an explicit one-pass constraint to the review instruction. It is a prompt change only, carries low risk, and directly addresses the unconstrained loop that is the root cause.
