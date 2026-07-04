# Digest cost & cross-day dedup — findings (2026-07-03)

Session investigating why run 221 cost $4.11 vs the ~$3 post-features baseline. Lean
summary; the value is the settled conclusions, not the trace.

## The cost spike is data-driven, not a regression
Root cause: the 07-03 deploy (`df7baa4`) raised the cross-day dedup threshold
`0.35 → 0.80`, which cut "filtered as duplicate" from **167 → 4**, so ~163 more articles
entered the pipeline (**452 → 627 into CLUSTER**). CLUSTER extraction is per-article Sonnet
= **O(articles)**, so cost scaled across every stage (cache_read ballooned: select
169K→762K, coherence 264K→595K, write 192K→481K tokens). Actual fetch volume was flat
(+4%); ~95% of the jump was the threshold change, not the news cycle.

Runs on the **subscription OAuth token** (`CLAUDE_CODE_OAUTH_TOKEN`), not a metered API key
— the "API-equivalent" dollar figure is **pool drawdown**, not a bill. The digest is
Sonnet+Haiku (no Opus), so it's a *minor* contributor to the Opus-cost-dominated weekly
meter. Optimise it for discipline, not because $1/day moves the meter much.

## The threshold change was correct — but it's a dead lever for cost
0.35 had a ~65% false-positive rate (dropping distinct stories); 0.80 fixed real
over-dropping. The extra cost is the price of **recall**. But:
- At 0.80 the pre-filter is **near-inert** (~4 drops/run). Cross-day redundancy is handled
  downstream by SELECT (`yesterday_headlines.txt`) + the **thread system** (continuations
  rendered as deltas). Both worked on run 221: dropped Nord Stream / OpenAI / Le Pen because
  they didn't advance; threaded Kyiv / Iran / Khamenei / France as updates.
- The article volume lives in the **0.35–0.55 band** (156 articles = the FP zone).
  0.80→0.55 drops only 13 more; →0.65 only 3. **No threshold is both cheap and correct.**
- On run 221 the recall insurance bought nothing visible — the extra ~163 articles were
  FPs, non-selected, or redundant cluster members. None were load-bearing for a selected story.

## Entity/event dedup: PoC'd — don't build it now
- Proxy PoC (deterministic, on run 221): entity-**count** matching (≥N shared) over-merges on
  ubiquitous actors (China+US+EU appear across distinct stories). Count is the wrong axis.
- The right signal is the pipeline's **IDF-weighted tag-bag + `primary_event` + time-kernel**
  — which CLUSTER already computes per article and then **discards**.
- But cross-day is already handled (threads), so an entity *pre-filter* re-solves a solved
  problem while adding over-merge risk. File it. Its real payoff is as a **persisted
  substrate** for SELECT/threads (they re-derive linkage today), not as a dedup filter.

## The real levers
- **Cost:** `CLUSTER_EXTRACT_MODEL=haiku` (the O(n) lever; ~10% coverage dip per prior eval —
  a product call, not a bug). Effort `medium` per stage. Lean context.
- **Quality (done):** blurb prompt fix in `select.md` — stop reciting the standing exclusion
  categories (sports/celebrity/lifestyle/US-domestic); name only *in-scope* same-day drops.
- **Quality (future):** **thread-aware SELECT** — split thread *linkage* to run before SELECT
  so tiering/ranking sees the computed delta (WRITE could then lead with the delta instead of
  writing a summary threads overwrite). Subsumes the "Kyiv toll-update at #1" concern. Real
  churn on a fresh subsystem — do it when you next open up threads, not now.

## Not worth pursuing now (with reasons, so it isn't re-litigated)
- **Fetching fulltext upstream** for the whole funnel: 400–600K tokens/stage + fetch latency
  + ~15% extraction failure + ToS exposure. Deltas live in the cheap headline/summary layer
  anyway. Keep the post-select fulltext gate.
- **Switching to GDELT / Event Registry:** GDELT is free but ~55% entity accuracy + firehose;
  Event Registry does our clustering at ~$49/mo but over *its* sources. Both surrender the
  curated, bias-rated source set that is the product. File GDELT as a possible free
  *enrichment signal* experiment; not a migration.
- **Delta-aware ranking as a prompt hack:** withdrawn. SELECT already gates inclusion on delta
  significance ("skip stories already well-covered unless significant new facts emerged") and
  it fired correctly. Kyiv's toll doubling wasn't thin. The residual (rank-by-importance not
  delta) is really the ordering item above.
