# Stats page — final design (2026-07-04)

The DECISION doc for the redesigned circulation `/stats` page. Supersedes the open questions in
`2026-07-04-stats-metrics-backlog.md` (the "what/why" research) and inherits the chrome contract in
`design-system.md` (§"Chrome — validated build" and §"Circulation chrome"). Where the backlog said
"consider", this says ship / roadmap / cut. Component names refer to the validated chrome set
(`scratch/chrome-mockups/stats_v2.html`).

Grounding note (verified against `migrations/20260101000000_baseline.sql` +
`20260316000000_add_run_usage.sql` + later ALTERs, not from memory):
- `shown_narratives(headline, tier, source_id, shown_at, run_id, original_title, cluster_id)` —
  **no** `published`, **no** bias/factuality/perspective (those are a static `sources.json` join),
  `cluster_id` not stable across days.
- `source_health(source_id, success, error_message, articles_fetched, articles_kept, recorded_at,
  run_id)` — **no** newest-item timestamp → feed *staleness* is NOT on-hand (roadmap-lite, 1 column).
- `run_usage(subagent, model, *_tokens, api_cost_usd, recorded_at, run_id, duration_ms)`.
- `digests(date, html, preheader, broadcast_recipients, broadcast_status, created_at, run_id, …)` —
  `broadcast_recipients` present → cost-per-subscriber is free.
- `dedup_log(article_title, article_source_id, matched_headline, similarity, threshold, action,
  logged_at, run_id)`.

Everything in "Ship now" is a `SELECT` over these + the compiled-in `sources.json` (circulation already
`include_str!`s it). No new columns, no webhooks, no labelled sets. That is the ship line.

---

## (a) The metric set to SHIP now

Verdicts on the current 5, then the cheap backlog adds that clear the on-hand bar.

| # | Metric | Verdict | Source (all period-windowed except catalog refs) |
|---|--------|---------|---------|
| 1 | Feed fetch-success | **KEEP** | `source_health.success` grouped by `source_id`, latest per source in window |
| 2 | Usage-by-tier | **REFRAME → source concentration** | `shown_narratives` source shares → HHI + effective-N |
| 3 | Recent-runs cost | **KEEP + extend** | `run_usage.api_cost_usd` ⋈ `digests.broadcast_recipients` ⋈ distinct-headline count |
| 4 | Dedup count + avg-similarity | **KEEP count, CUT avg-similarity** | `dedup_log` count where `action='filtered'` |
| 5 | Never-selected | **REFRAME → catalog coverage %** | `COUNT(DISTINCT source_id)` in window vs `|sources.json|`; never-selected = drill-down |
| 6 | **Shipped bias balance** | **ADD** (the premise) | `shown_narratives ⋈ sources.json.bias`; entropy + JSD vs catalog |
| 7 | **Geographic lens** | **ADD** (§c) | `shown_narratives ⋈ region-map`; geo-HHI + region breakdown |
| 8 | **Factuality-weighted coverage** | **ADD** (explains the balance ceiling) | `shown_narratives ⋈ sources.json.factuality` |

**Why avg-similarity is CUT, restated for the record:** it is mechanically floored by the threshold,
blind to false negatives (a real dupe at 0.34 never enters the stat), and *rewards* false positives
(two distinct wordy stories at 0.82 raise it). It cannot tell a good threshold from a broken one —
disqualifying given the live cross-day FP problem. Keep only the filtered **count** (a throughput fact,
honestly labelled as such, not as a quality signal).

**Deliberately NOT in the first ship** (on-hand data but heavier/fuzzier — fast-follow, not roadmap):
- **Cross-day novelty / recurrence** (mean self-information over `original_title`). Data is on-hand but
  it needs an IDF pass over history and its reader-facing meaning is fuzzy. Ship after the core lands.

Everything else (staleness, freshness latency, dedup F1, deliverability, engagement, story-geography)
is genuinely un-instrumented → **roadmap tiles**, §(e). Honesty rule: a metric only becomes a live tile
when its data exists; until then it is a dashed "Planned" tile naming exactly what it needs.

---

## (b) Component + rationale per shipped metric

The governing constraint (from `ui-design-craft` §1 + design-system §"Components"): **one alarm axis per
screen.** RAG hue (`--ok`/`--warn`/`--accent`) means *operational health only*. Every other axis —
political bias, factuality, geography, concentration — must carry meaning by **shape + text + a
non-alarm hue**, never by green/amber/red. Colour budget: the bias spectrum owns the low-sat
`--bias-l/c/r` trio; RAG owns the health block; everything else is monochrome + tabular text.

### 1. Feed health — RAG block (THE alarm axis)
- **Component:** the RAG health rows from the chrome set — `✓ Healthy / ▲ Degraded / ✕ Down` =
  shape-distinct glyph **+** `--ok`/`--warn`/`--accent` **+** the word **+** the % (redundant, survives
  greyscale and CVD). Per-source table below: mono uppercase headers, serif source name, mono
  tabular-nums numeric cells right-aligned, alpha-hairline rows (no zebra), hover wash.
- **Why:** feed-fetch is the *only* thing on this page that is genuinely operational (the class of the
  2026-06-16 outage). It earns the traffic-light convention; nothing else does. Degraded = fetched but
  `articles_kept` collapsed or partial failures in window; Down = `success=0` on the latest attempt.
- **One-alarm compliance:** ✅ this is the alarm axis. No other block may use these three hues.

### 2. Shipped bias balance — spectrum bar (shipped vs catalog)
- **Component:** the single spectrum bar (design-system §"Single spectrum bar"): 3 populated segments
  `--bias-l` slate / `--bias-c` grey / `--bias-r` terracotta (low-sat, non-partisan), **count printed on
  the paper** (never white-on-bar), sized by shipped L/C/R split. Under it: the **catalog** reference
  bar (the ceiling) + a mono label `N stories · X lean-left · Y center · Z lean-right`. One headline
  number: **normalized Shannon entropy (0–1)** and **JSD vs catalog** (JSD not KL — KL is undefined on
  empty buckets, which is most days).
- **Why:** "all sides at a glance" is the product's premise; balance is a comparison task the research
  says never to collapse. Framed **shipped-vs-catalog** (a declared normative target per RADio/Steck),
  which is fair to curation — it can't be blamed for a target it can't reach.
- **One-alarm compliance:** ✅ bias uses the reserved `--bias-*` trio, not RAG. A "good balance" must
  **never** be green — that would read as a verdict and fight the health axis.
- **Honest caption (required, not optional):** "The catalog spans only lean-left, center, and
  lean-right — there is no far-left/left/right/far-right source. 'All sides' is capped by the
  **factuality floor** (partisan extremes skew low-factuality and are excluded on quality), not by
  curation." Catalog ceiling is **15 lean-left / 17 center / 3 lean-right** (35 sources).

### 3. Factuality-weighted coverage — neutral ordinal meter
- **Component:** the neutral ordinal meter from the chrome set — `▮▮▮` / `▮▮▯` / `▮▯▯` (very-high /
  high / mixed) **monochrome** + a mono label + the shipped count per band. `unrated` sources
  (Hacker News) shown as a separate `▯▯▯` / "unrated" row, not folded into a band.
- **Why:** "no fluff" is a factuality claim; this is the evidence for it, and it *explains* the balance
  ceiling (the extremes are excluded on factuality, not silenced). It's the same cheap `sources.json`
  join as bias.
- **One-alarm compliance:** ✅ factuality is **ordinal**, not alarm → shape meter, single hue. Explicitly
  NOT green/amber (that would be a second alarm axis and a false "very-high = good/mixed = bad" verdict).

### 4. Source concentration — stat tile + top-sources table
- **Component:** a stat item (big serif number, mono caps label, sans sub) reporting **HHI (`Σ shareᵢ²`)**
  and the friendlier **effective number of sources = 1/HHI** ("effective ≈ N of M used"). Beside/under
  it a Gini figure. Then a top-sources table (source · stories · share%), mono tabular-nums, hairline
  rows, hover wash.
- **Why:** replaces the usage-by-tier leaderboard. A concentration index with a denominator beats a
  raw ranking — it answers "is the digest leaning on 3 wires?" A high HHI is a *finding*, not an alarm.
- **One-alarm compliance:** ✅ monochrome stat + table; no hue. `--accent-ink` only if a value crosses a
  stated concern line, and even then paired with text — but default is neutral.

### 5. Catalog coverage % — stat tile + never-selected drill-down
- **Component:** a stat item "**N of 35 sources used**" over the window + the % as the big number; below,
  a collapsed `<details>` "never selected in window (K)" listing the dormant sources (serif name, mono
  last-seen). Denominator = `|sources.json|` (period-independent).
- **Why:** reframes never-selected from a shaming tail into a **rate with a denominator** — the honest
  question is "how much of the catalog is actually earning its place," and the tail is the drill-down,
  not the headline.
- **One-alarm compliance:** ✅ neutral stat + list. Dormant ≠ broken (that's the RAG block's job); do not
  colour a never-selected source red.

### 6. Cost — recent-runs table + two derived stat tiles
- **Component:** recent-runs table (date · articles kept · recipients · API-equiv cost), mono
  tabular-nums, hairline rows. Plus two stat items: **cost / subscriber** (`Σ api_cost_usd ÷
  digests.broadcast_recipients`) and **cost / story** (`Σ api_cost_usd ÷ COUNT(DISTINCT headline)`).
- **Why:** cost is already computed; the two ratios turn an absolute into a unit-economics signal that
  survives list growth. `api_cost_usd` is API-equivalent (actual is $0 on subscription) — label it so.
- **One-alarm compliance:** ✅ neutral. `--accent-ink` on a value only with a stated budget line + text.

### 7. Dedup throughput — single stat tile (count only)
- **Component:** one stat item "**K near-duplicates filtered**" over the window (`dedup_log` where
  `action='filtered'`), mono caps sub "cross-day title TF-IDF pre-filter, threshold 0.80".
- **Why:** a throughput fact, honestly scoped. The quality of the filter (precision/recall) is a
  **roadmap tile** (§e #3) — this tile must not imply the filter is *correct*, only that it ran.
- **One-alarm compliance:** ✅ neutral single figure.

---

## (c) The geographic lens — concrete spec

The geographic analog of bias balance: "whose eyes are you seeing the world through?" Same pattern
(sources.json join + a static map, no instrumentation), shown as **shipped** distribution over the
window with the **catalog** as the ceiling — exactly like bias.

### The taxonomy — `perspective` → 6 regions (the static map)

`perspective` is unusable raw (19 values over 35 sources, singletons, and it **mixes geography with
topic**: `tech`, `global_tech`, `wire_service`, `western`, `western_finance` are not places). **Decision:
map the `perspective` *label*, not scraped HQ** — it's the field we have, it's deterministic, and it
avoids unverifiable per-source web research. The ~5 non-geographic labels are assigned to a region by
the outlet's editorial home, documented explicitly below (this is the one place judgment enters; it is
frozen in the map, not recomputed).

| Region | `perspective` values folded in | Sources | n |
|--------|-------------------------------|---------|---|
| **Europe** | `british`, `german`, `french`, `western`, `western_finance` | BBC, Der Spiegel, DW, Le Monde, Economist ×5, Guardian, FT | **11** |
| **N. America** | `american`, `canadian`, `tech` | NPR, NYT, WaPo, WSJ, CBC, Globe & Mail, Ars Technica, Hacker News, The Verge | **9** |
| **Asia-Pacific** | `asian`, `japanese`, `singaporean`, `asia_pacific`, `filipino`, `indian` | SCMP ×3, Nikkei, Straits Times, The Diplomat, Rappler, The Hindu | **8** |
| **Middle East** | `middle_east`, `israeli` | Al Jazeera, Al-Monitor, Haaretz ×2 | **4** |
| **Global** | `wire_service`, `global_tech` | Reuters, Rest of World | **2** |
| **Africa** | `south_african` | Daily Maverick | **1** |

Documented judgment calls (the non-geographic folds): `western`/`western_finance` → **Europe** (the
Economist / Guardian / FT are UK-based); `tech` → **N. America** (Ars / HN / Verge are US tech press);
`wire_service` + `global_tech` → **Global** (Reuters and Rest of World are genuinely transnational, no
single home). Total = 35 ✓. Implement as a `HashMap<&str, Region>` keyed on `perspective` in
`circulation`; a source whose `perspective` isn't in the map falls to **Global** with a debug-log warn
(so a new source can't silently vanish).

### The headline number — geographic HHI (reuse the concentration math)

Reuse the same HHI as source concentration, over region shares. Catalog ceiling:
shares = [11, 9, 8, 4, 2, 1]/35 → **HHI ≈ 0.234**, **effective regions = 1/HHI ≈ 4.3 of 6**. Report as
"**Effective regions ≈ 4.3 / 6**" (interpretable) with HHI in the sub. Shipped value recomputed over the
window; catalog value is the ceiling shown alongside.

### The breakdown — sorted region rows, monochrome (NOT a map)

- **A sorted region breakdown, not a choropleth.** A world map for ~6 source-count buckets is
  over-engineered, hard to compare small values, and a11y-hostile — rejected (design-skill "honest
  skips"). Each region is a table row: **region label (mono)** · **count (mono tabular-nums)** · a
  **single-hue muted bar** (greyscale / one muted ink), sorted descending.
- **Monochrome, never the bias colours, never RAG.** Geography is **categorical**, not alarm/ordinal.
  Colour is already spent on the bias spectrum, so geography stays one muted hue with the **count as the
  redundant text** (never colour-only). Same table craft as the rest: alpha-hairline rows, no zebra.

### The honest caption (required)

"This is **source-origin** diversity — where outlets are *based*, not where the news *happened*. The
source base skews **Western/Anglo** (Europe + N. America = 20 of 35, ~57%; Africa is a single outlet).
A diverse source base *tends* toward diverse coverage but doesn't guarantee it — true
**story-geography** (is the digest covering global events or just Western ones?) needs per-story
geo-tagging we don't yet have (see roadmap)." Surface the skew the way the bias block surfaces the
factuality floor — don't sell diversity we don't have.

---

## (d) The period toggle — 7d / 30d / All

- **Control:** the **raised segmented control** from the chrome set (track = `--wash` + hairline + 3px
  pad; active = `--panel` pill + `--ink` text + tiny shadow). **Do not** fill the active segment with
  accent (the white-on-accent legibility/reserve problem the chrome build already fixed). Segments:
  `7 days` · `30 days` · `All`. **Default = 30 days** (a month reads as "recent behaviour" without
  single-run noise).
- **Mechanism:** a server param `?period=7d|30d|all` (default `30d`), validated against an allowlist
  (reject → default; never interpolate raw). Each windowed query gets a `WHERE <ts> >= …` clause:
  - `7d` → `datetime('now','utc','-7 days')`, `30d` → `-30 days`, `all` → no lower bound.
  - **Use the right timestamp per table** (they differ): `shown_narratives.shown_at` (bias, geography,
    concentration, coverage), `run_usage.recorded_at` / `digest_runs.run_at` (cost, recent runs),
    `dedup_log.logged_at` (dedup), `source_health.recorded_at` (health). This is the load-bearing
    gotcha — a single hardcoded column name breaks a subset of tiles silently.
- **What does NOT move with the period:** the **catalog references** — the 35-source denominator, the
  bias ceiling (15/17/3), the geographic ceiling (4.3/6). They're properties of `sources.json`, not the
  window; render them period-independent so the "shipped vs ceiling" framing stays honest at every
  setting.
- The active period is echoed in the masthead sub-row mono stat (e.g. `LAST 30 DAYS · 62 STORIES`) so
  every number on the page has a visible denominator/scope.

---

## (e) "Planned" roadmap tiles — un-instrumented metrics

Each is a **dashed-border + faint-warm-tint tile + mono `Planned` badge + a one-line note stating
exactly what data it needs** (design-system §"Roadmap tiles"). Honest about the gap; no fake numbers.
Ordered by value ÷ effort.

| Tile | What it would show | Exactly what it needs |
|------|--------------------|-----------------------|
| **Feed staleness** | now − newest-item age per feed (a stalled-but-"successful" feed) | **1 new column** `source_health.newest_item_at` (already parsed in `feeds.py`, just persist it) |
| **Publish → surface latency** | freshness: median / p90 hours from article `published` to shown, split by tier | **1 new column** `shown_narratives.published` — stamp the in-pipeline `published` at write time (don't reconstruct a fuzzy `(run_id, source_id, title)` join) |
| **Dedup precision / recall / F1** | the real replacement for the cut avg-similarity; can finally say whether 0.35→0.80 helped | **A labelled pair set** (~100–300 pairs oversampled near the boundary), scored with **B-Cubed F** (Amigó 2009 — sidesteps the ARI self-agreement-band problem hit on CLUSTER) |
| **Deliverability** | hard-bounce rate (<0.5–1%) + spam-complaint rate (Google/Yahoo throttle ≥0.3%) — a floor you can't currently see | **A Resend webhook endpoint + an `email_events` table** (Resend emits `bounced`/`complained` natively) |
| **Engagement / list-health** | CTR (MPP-proof) + unsubscribe rate | **Link-redirect tracking** + an unsubscribe-events sink. *Avoid* raw open-rate/CTOR (MPP-inflated vanity) and raw subscriber count |
| **Story-geography** | where the news *happened* (vs source-origin) — closes the loop the geographic lens caveat leaves open | **Per-story geo-tagging** in the newsroom pipeline (a real BUILD, not a join) |

Retired, not roadmapped: reader up/down feedback — the Yes/No vote was removed this session, so
`story_feedback` gets no new data and the table is being retired.

---

## Ship order (value ÷ effort)

1. Concentration + coverage (replace usage-by-tier + never-selected) + keep health/cost/dedup-count —
   pure reframes over existing queries.
2. Shipped bias balance (the premise) + factuality meter — one `sources.json` join, two tiles.
3. Geographic lens — the region map + geo-HHI + monochrome breakdown + honest caption.
4. Period toggle wired through every windowed query (per-table timestamp).
5. Roadmap tiles (static, honest) for the six un-instrumented metrics.

Then fast-follow: cross-day novelty (on-hand, needs an IDF pass); then the roadmap instrumentation in
the table order (staleness → freshness → dedup F1 → deliverability → engagement → story-geography).
```
