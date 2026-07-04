# Stats metrics — backlog & rationale (2026-07-04)

Grounds the stats-page redesign (`scratch/chrome-mockups/stats_v2.html`). From two research passes:
a literature dig on curation/newsletter quality metrics + a codebase data-audit. This is the
actionable spec — what to cut, keep, add, and what data each needs.

## Verdict on the current 5 metrics

- **CUT — dedup "avg cosine similarity of filtered pairs."** Circular and misleading: mechanically
  floored by the threshold, blind to false negatives (a true dupe at 0.34 never enters the stat), and
  it *rewards* false positives (two wordy-but-distinct stories at 0.82 raise the number). It cannot
  distinguish a good threshold from a broken one — damning given the live 65%-FP cross-day dedup
  problem. Keep the filtered **count** (throughput) only.
- **KEEP — fetch-success rate** (add feed *staleness*: now − newest-item age) and **recent runs**
  (kept / recipients / cost).
- **REFRAME — usage-by-tier → concentration (Gini/HHI)**; **never-selected → coverage rate**
  ("X of 35 used in 30 days", never-selected as drill-down). A rate with a denominator beats a
  leaderboard/tail.

## Add — ranked by value ÷ effort

**Cheap wins (data already in the DB; only a `sources.json` bias/factuality join):**
1. **Shipped bias-distribution balance** — the product's premise. Normalized Shannon entropy (0–1)
   and **JSD vs. the catalog** (use JSD not KL — KL is undefined on empty buckets, which is most
   days). Frame as *shipped vs catalog* (fair to curation) + the catalog's own ceiling.
2. **Source concentration** — HHI (`Σ share²`) + Gini over `shown_narratives` source shares.
3. **Catalog coverage %** — `|⋃ selected sources| / |catalog|` over the window.
4. **Cross-day recurrence / novelty** — mean self-information over `original_title` history.
5. **Cost per subscriber / per story** — extend the existing `run_usage` cost query with
   `digests.broadcast_recipients` and distinct-story counts.
6. **Factuality-weighted coverage** — shipped source mix over the `factuality` field ("no fluff" is a
   factuality claim). Ties to the balance ceiling: the extremes are excluded on factuality.

**One-column add:**
7. **Publish→surface latency** (freshness, median/p90, split by tier). The article `published` time
   flows through `feeds.py`/`prepare.py` but isn't persisted — stamp it onto `shown_narratives`
   instead of reconstructing a fuzzy `(run_id, source_id, title)` join to `fetched_articles`.

**Needs a labelled set:**
8. **Dedup precision/recall/F1 + threshold sweep** — the real replacement for the cut metric, and the
   only thing that can say whether 0.35→0.80 helped. Needs ~100–300 labelled pairs oversampled near
   the boundary. Score with **B-Cubed F** (Amigó et al. 2009), which sidesteps the ARI
   self-agreement-band problem hit on CLUSTER.

**Needs new tracking (roadmap):**
9. **Deliverability** — hard-bounce rate (<0.5–1%) + spam-complaint rate (Google/Yahoo throttle at
   ≥0.3%). A floor you currently can't see (the class of the 2026-06-16 outage). Needs a Resend
   webhook endpoint + an events table; Resend emits `bounced`/`complained` natively.
10. **Engagement / list-health** — CTR (MPP-proof; needs link-redirect tracking) + unsubscribe rate.
    **Avoid** raw open rate / CTOR (MPP-inflated vanity) and raw subscriber count (vanity).

**Dropped:** reader up/down feedback — the Yes/No vote was removed this session, so `story_feedback`
gets no new data and the table is being retired.

## Data-audit facts (what's computable today)

- **On-hand:** `shown_narratives` (source_id, tier, shown_at, headline, original_title) ⋈
  `sources.json` (7-point bias + factuality + perspective per source) → shipped bias/factuality mix.
  `source_health`, `digest_runs`, `run_usage` (cost + `duration_ms`), `digests.broadcast_recipients`
  (a daily reach snapshot = a growth series), `dedup_log` (every filter decision).
- **Gotchas:** `shown_narratives` is **one row per source per story** → any per-story metric must
  `COUNT(DISTINCT headline)` / group on `cluster_id`. Bias/factuality are **not DB columns** — static
  `sources.json` join (unrated/changed sources won't map). `cluster_id` is not stable across days.
- **Not persisted:** article `published` time (in-pipeline only), coherence pass-rate (only inside
  `run_artifacts.content` JSON blobs), email engagement (nothing beyond send status).

## Structural finding (product, not code)

The catalog spans only `{lean-left, center, lean-right}` — zero far-left/left/right/far-right. "All
sides" is capped by the **factuality floor** (extremes skew low-factuality, excluded on quality), not
by curation. The balance viz must state this; measuring against a uniform-7 target would be
dishonest. Chosen target: **shipped vs catalog** (declared normative choice, per RADio/Steck).

## Suggested build order (value ÷ effort)

concentration + coverage (on-hand, replaces 2 current tiles) → shipped bias balance (on-hand, the
premise) → freshness latency (1 column) → dedup P/R/F1 (labelled set; your live problem) →
deliverability webhooks → per-story blindspot + Cdet as the labelled-data roadmap.

## Sources

Amigó et al. 2009 (B-Cubed / clustering eval); Vrijenhoek et al. RADio RecSys'22 + Steck RecSys'18
(balance divergence, JSD>KL); Kaminskas & Bridge TiiS'16 (coverage/novelty/diversity); Fleder &
Hosanagar Mgmt Sci'09 (concentration); NIST TDT (latency/Cdet); Google/Yahoo 2024 bulk-sender
(deliverability); NN/g + Postmark/beehiiv (vanity metrics, MPP kills opens). Full per-claim cites in
the session research syntheses.
