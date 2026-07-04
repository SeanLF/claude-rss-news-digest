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

## Geographic lens (added 2026-07-04 — Sean's Q)

**Add: source-origin geographic diversity** — the geographic analog of the bias-balance metric
("all sides" is geographic as well as political). CHEAP (sources.json join + a region map, no new
instrumentation), same pattern as bias-balance.

**Data reality:** the `perspective` field in sources.json is NOT usable raw — 19 distinct values over
35 sources, mostly singletons, and it **mixes geography with topic** (`tech`, `global_tech`,
`wire_service`, `western_finance` aren't places). Regroup into coarse regions (N.America / Europe /
Middle East / Asia-Pacific / Africa / Global) via an explicit map, exactly like L/C/R bias bucketing.

**Caveat (state it on the stats page):** this is **source-origin** diversity (where outlets are
based), NOT **story-geography** (where the news happened). The latter — "is the digest covering global
events or just Western ones?" — needs per-story geo-tagging we don't have (would be a real BUILD, a
newsroom pipeline change). Source-origin is a proxy: a diverse source base *tends* toward diverse
coverage but doesn't guarantee it. Don't over-claim it as "coverage geography."

### Geographic lens — how to show it (UX) + the story

**The story it tells:** "Whose eyes are you seeing the world through?" — "all sides" is geographic, not
just political. And the honest finding the data will show: the source base **skews Western/Anglo**
(perspective counts: western 6 + american 4 + british/canadian/german/french ≈ Anglo-Euro heavy; vs a
thinner non-Western spread — middle_east 2, asian 3, israeli 2, japanese/filipino/indian/singaporean/
south_african/asia_pacific singletons). Surface that skew honestly, the way the bias page states the
factuality floor. Don't sell diversity we don't have.

**UI (grounded in the design skill):**
- **A sorted region breakdown, NOT a map.** Coarse buckets (N.America / Europe / Middle East /
  Asia-Pacific / Africa / Global), each a row: region label (mono) + count + a **muted bar**, sorted
  desc. A choropleth world map for ~6 source-count buckets is over-engineered, hard to compare small
  values, and a11y-hostile — reject it (design-skill "honest skips").
- **Monochrome, not the bias colours, not RAG.** Geography is categorical, not an alarm/ordinal axis
  (§1: one alarm axis, reserve hue). Colour is already spent on the bias spectrum → geography stays a
  single muted hue / greyscale bar, with the **count as the redundant text** (never colour-only).
- **One headline number:** a geographic concentration/diversity measure (reuse the HHI/Gini already
  planned for source concentration) — "how concentrated is the source base geographically" — + the
  regional breakdown beneath it + a one-line honest caption naming the Western skew.
- Same redundant-coding + table-craft rules as the rest of stats (mono label, tabular-nums count,
  alpha-hairline, no zebra).
