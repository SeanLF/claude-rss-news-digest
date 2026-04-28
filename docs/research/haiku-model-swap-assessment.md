# Haiku Model Swap Assessment: RECAP + COHERENCE Subagents

**Date:** 2026-04-03
**Question:** Can Haiku 4.5 replace Sonnet 4.6 for the RECAP and COHERENCE subagents without meaningful quality loss?
**Scope:** news-digest pipeline, 18 production runs analysed.

---

## TL;DR

Switch COHERENCE to Haiku: yes, with a caveat. Switch RECAP to Haiku: yes, low risk. Combined saving: ~$0.56/run (~13% of run cost), ~$17/month, ~$205/year.

---

## Subagent Task Descriptions

### RECAP

**What it does:** Reads `recent_rss_titles.csv` (~316 RSS titles from the past 7 days) and produces a 2-3 sentence thematic paragraph summarising major news themes. Output goes to `recap.txt`.

**Downstream use:** The recap feeds into the SELECT subagent to give it editorial context about what topics have been prominent recently, helping it de-prioritise stale stories.

**Output quality bar:** Fluent English paragraph, thematic language only (no specific headline reproduction), 2-3 sentences. This is a summarisation task with a low ceiling requirement -- the summary is context, not published copy.

### COHERENCE (labelled `fact-check` in DB)

**What it does:** Reads `draft_selections.json` (all written headlines + article_id references) and all `articles_*.csv` files (full article summaries). For each headline, verifies it accurately represents its source articles. Outputs `coherence_report.json` with pass/fail + reason per headline.

**Downstream use:** The dispatcher drops any headline marked `pass: false` before calling `write_selections`. Failed coherence checks are the last quality gate before the digest is assembled.

**Output quality bar:** Structured JSON with correct schema, accurate cross-referencing between headlines and article summaries, strict fabrication detection. Higher stakes than RECAP.

---

## Token Usage (Production Data)

Data from `run_usage` table, 18 runs (March--April 2026). RECAP has only 5 observations (newer subagent); COHERENCE has 18. One RECAP run (run 124) is excluded as an outlier (1.85M cache_read tokens vs. normal ~36K -- likely a retry loop or agent malfunction).

| Subagent | Runs | Avg Input | Avg Output | Avg Cache Read | Avg Cache Write | Actual Avg Cost (Sonnet) |
|---|---|---|---|---|---|---|
| RECAP | 4* | 487 | 618 | 36,012 | 37,905 | $0.070/run |
| COHERENCE | 18 | 898 | 17,022 | 344,324 | 88,982 | $0.695/run |

*Excluding outlier run 124 (cache_read = 1,848,078, cost = $2.09).

**Key observation:** COHERENCE is dominated by cache_read tokens (~344K average). This makes sense -- it reads all `articles_*.csv` files on every run; these are large and cached. RECAP is a lightweight task by comparison.

---

## Cost Calculation

### Pricing (per million tokens)

| Token type | Sonnet 4.6 | Haiku 4.5 | Ratio |
|---|---|---|---|
| Input | $3.00 | $0.80 | 0.267x |
| Output | $15.00 | $4.00 | 0.267x |
| Cache read | $0.30 | $0.08 | 0.267x |
| Cache write | $3.75 | $1.00 | 0.267x |

The price ratio is uniformly ~0.267 across all token types, so projected Haiku cost = Sonnet cost x 0.267.

### Projected costs

| Subagent | Sonnet/run (actual) | Haiku/run (projected) | Saving/run | Saving % |
|---|---|---|---|---|
| RECAP | $0.070 | $0.019 | $0.051 | 73% |
| COHERENCE | $0.695 | $0.185 | $0.510 | 73% |
| **Combined** | **$0.765** | **$0.204** | **$0.561** | **73%** |

### Run-level impact

Average full run cost (all subagents): **$4.43/run**

After switching both to Haiku: ~**$3.87/run** -- a **12.7% reduction** in total run cost.

| Period | Saving |
|---|---|
| Per run | ~$0.56 |
| Monthly (30 runs) | ~$16.80 |
| Annual (365 runs) | ~$205 |

Note: The task prompt cited ~$0.77/run for these two subagents (~15%). The DB data shows $0.765/run (very close). The slight difference in overall % (13% vs. 15%) reflects actual run cost distribution.

---

## Complexity Assessment

### RECAP: Low complexity, low risk

The task is: read a CSV of ~316 RSS titles, produce 2-3 thematic sentences. This is a single-pass summarisation with no structured output schema, no cross-referencing, and no verification logic. Haiku reliably handles summarisation tasks of this scale. The output bar is low -- the sentences just need to be coherent and on-topic. A weaker summary is a minor editorial nuisance, not a pipeline failure.

**Verdict: switch to Haiku. Negligible quality risk.**

### COHERENCE: Moderate complexity, moderate risk

The task requires:
1. Reading multiple files (draft_selections.json + all articles_*.csv)
2. Cross-referencing each headline against specific article_ids
3. Detecting fabricated specifics (numbers, names, dates not in source)
4. Outputting valid JSON matching a schema

This is more demanding. The main risks of a weaker model:
- **False negatives** (missing a fabrication): A bad headline passes through and appears in the digest. This is the real failure mode.
- **False positives** (correctly written headline fails): Causes a valid story to be dropped. Annoying but not harmful.
- **Schema errors**: The JSON output could be malformed, causing the dispatcher's verification step to retry the agent.

However, context helps here: Haiku 4.5 is substantially stronger than Haiku 3.5 (the model originally noted in project memory as having been used before). The coherence check operates on article summaries that are already concise (the articles_*.csv fields), so the actual reading comprehension load is moderate. The structure of the check (does headline X match article Y?) is pattern-matching, not inference.

The fact that the dispatcher has a retry mechanism (retry once on invalid JSON) reduces the schema failure risk. The meaningful risk is false negatives on fabrication detection.

**Quality risk is real but manageable.** The WRITE subagent (still on Sonnet) produces the headlines, and its output tends to be conservative. Haiku should catch obvious fabrications. Subtle ones are the gap.

**Verdict: switch to Haiku, but monitor closely for the first 2 weeks. If coherence fail-rate drops noticeably (currently close to 0 -- most runs show all-pass), investigate.**

---

## Recommendation

**Switch both RECAP and COHERENCE to Haiku 4.5.**

Rationale:
- RECAP is trivially within Haiku's capabilities. No quality argument for keeping it on Sonnet.
- COHERENCE carries more risk but the task structure (file read + cross-reference + JSON output) is something Haiku 4.5 handles well. The pipeline already has a retry gate. The WRITE subagent staying on Sonnet means the content to be checked is high quality, reducing the cognitive load on the checker.
- The saving ($0.56/run, 13% total) is real and compounds daily. At current run frequency this is ~$205/year with no architectural change.
- Project memory notes Haiku was originally intended for these two agents -- switching back aligns with the original design intent.

**If switching only one:** RECAP first (zero-risk, $0.05/run saving) and observe COHERENCE on Haiku for a week before committing. COHERENCE is where 91% of the combined saving lives.

---

## Caveats

1. **RECAP outlier (run 124):** One run showed 1.85M cache_read tokens and $2.09 cost -- ~29x the normal cost. Cause unknown (possibly a retry that read the full articles_*.csv set instead of just recent_rss_titles.csv, or a model hallucination loop). This risk exists regardless of model, but a less capable model may be slightly more prone to errant tool use. The dispatcher's retry logic should catch it.

2. **Haiku 4.5 vs. Haiku 3.5:** Project memory's original Haiku notes predate the 4.5 model. The capability gap between Haiku 4.5 and Sonnet 4.6 is smaller than the gap between Haiku 3.5 and Sonnet. This assessment uses Haiku 4.5 pricing and assumes 4.5 capability.

3. **Cost projections assume token volumes stay constant.** If article volume grows (more feeds, more articles), COHERENCE cache_read tokens grow proportionally. The saving % stays at 73% regardless of volume.

4. **Schema validation already in place:** The MCP server's `SOURCE_SCHEMA` + the dispatcher's JSON verification step provide a safety net for malformed coherence output. The retry mechanism means a single schema failure doesn't abort the pipeline.
