# Source diversity: decision + verified slate (2026-07-04)

Status: **decided, not yet implemented.** Independent of the chrome/redesign port — can be
landed in any session. Verified feeds below all returned current (Jul 3–4 2026) items.

## Why this exists

Triggered by "should we add digg.com/tech, and are we politically/geographically balanced?"
The investigation (RSS lineup + 60-day prod-DB behaviour) reframed the question: the real gaps
are **geographic**, not political, and the highest-leverage move is a small set of verified adds
inside the existing high-factuality bar.

## Key findings (the reasons behind the slate)

1. **Editorial selection already corrects the left tilt.** From `shown_narratives` (last 60d):
   shown-story bias is **42.5% lean-left / 42.0% center / 15.5% lean-right**, vs the roster's
   42.9 / 48.6 / 8.6. Lean-right *doubles* its roster share in what readers see (Straits Times
   drives it). So political left/right balance is largely a solved problem — SELECT flattens, it
   does not amplify. (Caveat: this is *source-label* bias, not per-story framing.)
   → **Adding Anglosphere-right sources (National Post, The Dispatch, Reason) is low priority** and
   would deepen the Anglosphere concentration the rebrand's geographic-lens stat is about to display.

2. **Geography is the real hole.** Delivered mix (shown, 60d): Asia 31%, North America 17%,
   wire/global 15%, Middle East 13%, Europe 17% (split desk + national), Africa **2.4% (one
   channel)**, **Latin America 0% (no source at all)**. LatAm and Africa-beyond-South-Africa are
   the genuine blind spots — and both become *publicly visible* on the new stats geographic-lens tile.

3. **No cost lever to "fund" adds — and none needed.** An earlier analysis wrongly flagged the 5
   Economist feeds as "54% of cluster cost" — that summed `source_health.articles_fetched` (the raw
   300-item feed cap, refetched whole every run and then dropped by the `pub > last_run` freshness
   filter in `feeds.py:191`). On the correct column, **`articles_kept`**, the Economist is ~224
   articles / 60d = **0.6% of cluster input**, and it *converts fine* (224 kept → 52 shown, 23% hit
   rate, on par with Reuters). **Do not trim the Economist.** Real cluster input ≈ 580 articles/run;
   a new geographic source adds tens/run — negligible. Just add.

   → **Stats-page correction:** any per-source volume/geographic-lens metric must read
   **`articles_kept`, not `articles_fetched`** — the raw column would report the Economist as the
   dominant source when it is ~0.6% of what actually flows through the pipeline.

## The slate

All bias mapped MBFC → Ground News 7-point. Factuality bar is HIGH/VERY-HIGH; "Mostly Factual"
is one notch below and only acceptable as a flagged regional fill.

### Confirmed — wire these (clean HIGH bar, English, verified live feeds)

```json
{"id": "mexico_news_daily", "name": "Mexico News Daily", "url": "https://mexiconewsdaily.com/feed/", "bias": "lean-left", "factuality": "high", "perspective": "mexican"},
{"id": "buenos_aires_times", "name": "Buenos Aires Times", "url": "https://www.batimes.com.ar/feed", "bias": "lean-right", "factuality": "high", "perspective": "argentine"}
```

- **Mexico News Daily** — Mexico. ~10–15 items/day. Best single LatAm pick; clears HIGH cleanly.
- **Buenos Aires Times** — Argentina. ~25–40/day (full-site firehose — no world-section feed
  exists; expect local sport/culture noise, which CLUSTER/SELECT will ignore). Bonus: a rare
  lean-**right** non-US voice, so it nudges the tilt the right way while filling the Southern Cone.

Net bias effect of the two: +1 lean-left, +1 lean-right — self-balancing.

### Pending Sean's call

- **AllAfrica** (pan-African) — MBFC Least Biased → **center**, **HIGH**. Feed:
  `https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf` (RDF/RSS1.0). The **only**
  clean center+HIGH continental option (Kenya's Nation/Standard, Egypt's Ahram all failed at
  *Mixed*; Africa Confidential is paywalled). **Caveat: it's an aggregator** (the digg problem in
  miniature — republishes other outlets, so its bias label is a blend and it adds within-run
  dedup load). Recommendation: **take the full pan-African feed** and let the existing within-run
  URL/title dedup handle republish overlap; fall back to country sub-feeds (`/rdf/nigeria/`,
  `/rdf/kenya/`) only if the noise proves annoying. Proposed:
  `{"id": "allafrica", "name": "AllAfrica", "url": "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "bias": "center", "factuality": "high", "perspective": "pan_african"}`
- **Premium Times** (Nigeria) — lean-left, **Mostly Factual** (flag). Recommendation: **skip** —
  AllAfrica already covers Nigeria and this is below the bar.

### Rejected (with reasons)

- **digg.com/tech** — an aggregator; no public RSS feed (links are internal redirect stubs to X
  posts + arXiv); its stories are reposts of sources you already carry (HN overlap). Directly
  fights the committed dedup/canonical-resolution direction (`b912405`, `a8d64ed`, GN-link work).
  MBFC's "Mostly Factual / Left-Center" rates the *legacy editorial* Digg, not the algorithmic tech
  crawler — the score doesn't transfer. Capture its value by adding the primary sources it surfaces
  directly (arXiv cs.CL/cs.AI, lab blogs) if a stronger AI strand is wanted.
- **Times of Israel** — hypothesis (center/High non-left Israeli voice) did **not** hold: MBFC
  rates it lean-left / Mostly Factual — same lean as existing Haaretz ×2, and below the bar. Adds
  no perspective diversity to the Israeli slot. A genuine center/High Israeli voice wasn't found.
- **Rio Times, The Africa Report** — Mostly Factual; redundant with the picks above.
- **MercoPress, Ahram Online, Daily Nation/The Standard, Africanews** — Mixed factuality, below bar.

## How to land it (safe path — verified against the working tree 2026-07-04)

`newsroom/sources.json` is **clean** and untouched by the in-flight redesign/translation streams —
a source add is textually isolated. Discipline needed:

1. **Worktree off `main`** (not the `design/chrome-redesign-port-handover` branch — wrong home,
   loaded with two half-integrated feature streams). `git worktree add` a fresh branch off `main`.
2. Add the confirmed entries (+ AllAfrica if Sean approves) to `newsroom/sources.json`.
3. Put the feed-parse test in a **new** file `newsroom/tests/test_sources.py` — **not**
   `test_run.py`, which is dirty with translation-stream WIP.
4. `make test`, then commit (per TDD: failing test first if practical).

## Wiring the geographic-lens stat (belongs with the port, not standalone)

The new perspective values need mapping to region buckets for the stats geographic-lens tile,
or the new sources won't categorize:
- `mexican`, `argentine` → **Latin America**
- `pan_african` → **Africa**

Check `REGION_CONFIG` / region mapping (referenced in db/render for signals rendering) and the
stats-design doc. This half depends on the geographic-lens stat, which is unbuilt — do it when the
port session builds that stat.
