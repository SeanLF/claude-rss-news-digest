# Port implementation handover — apply the redesign to circulation + newsroom

**Read this first, then `design-system.md`.** The design is done: seven validated mockups
(Lighthouse a11y/BP/SEO 100, light+dark, ≥390px, provably consistent), a canonical token file,
and a design spec. This doc is the bridge to implementation ("Path B") — it maps every surface and
**every interaction to a real route/query or flags it "needs building,"** so a fresh session can
start porting without re-deriving the current state.

Nothing here is in production yet. All design artifacts are uncommitted; `scratch/` is gitignored.

## 0. Orientation

- **Two consumers.** `circulation/` (Rust/Axum) renders the app chrome + injects chrome around the
  stored digest blob. `newsroom/` (Python) renders the digest blob itself (also the email).
- **The design's core principle:** app *chrome* (index/sources/stats/threads/thread-detail/feedback)
  is one soft modern-editorial system; the *digest document* keeps its own sharper wire severity and
  its own token set. This divergence is deliberate and documented — do not "unify" it away.

## 1. Artifact index (what to port *from*)

| Artifact | Role |
|---|---|
| `docs/design-system.md` | **THE spec** — tokens, type scale, every component, the "Cross-page chrome contract", the theme-toggle spec, the token/CSS/font delivery model, a11y checklist, CSS pitfalls. **Authoritative sections:** "Chrome — validated build (2026-07-04)", "Cross-page chrome contract", "Digest-view frame + thread detail", "Token source of truth + production delivery". The earlier "Circulation chrome" *migration sketch* is **superseded** where they differ (esp. radius: chrome is soft 6/8px, not the sketch's flat/3px) — don't port from the sketch. |
| `scratch/chrome-mockups/*.html` | The eight target renders: `chrome_v12` (index, with the segment + date-jump wired), `sources_v2`, `stats_v2`, `threads_v1`, `thread_detail_v1`, `feedback_v1`, `search_v1` (search results), `digest_v1`. Self-contained; open in a browser. (Gitignored `scratch/` — regenerable via `build.py`; `design-system.md` is the durable authoritative spec.) |
| `scratch/chrome-mockups/build.py` | Reproducible generator for the mockups (fonts, tokens, toggle, per-page CSS). The source of truth for the mockups' markup/CSS. |
| `scratch/chrome-mockups/cssdiff.js` | Cross-page computed-style diff — the consistency gate (`node cssdiff.js`). Keep it green as you port. |
| `design/tokens.css` | Canonical design tokens (both token families + dark + `[data-theme]`). The single source `include_str!`'d into circulation and read by newsroom. |
| `docs/2026-07-04-chrome-design-research-and-language.md` | Why the design is what it is (evidence). Consult on any "should it look like X" question. |
| `docs/2026-07-04-stats-metrics-backlog.md` | The stats-page metrics rethink (a separate engineering track). |
| `bin/lighthouse`, `bin/a11y-check`, `make lighthouse`/`make a11y` | Verification gates. `bin/lighthouse` points at the mockups today; retarget at circulation routes (`--url`) once ported. |

## 2. Foundation — token / CSS / font delivery (do this FIRST; it unblocks everything)

**Current state (verified):** circulation CSS is 100% inline Rust string consts with the `:root`
token block **duplicated in every template** (`index.rs:51-75`, `search.rs:84-104`, …). No fonts are
served, no `@font-face`, no `/assets` route — typography is system Georgia/sans only. Binary assets
already use `include_bytes!` (`handlers.rs:252,278`) — the precedent to follow. Newsroom injects
`newsroom/templates/digest.css` into the digest via a `{{STYLES}}` string replace (`render.py:394-410`),
and `resolve_css_variables` (`render.py:134-163`) inlines vars to literal hex **for email only**.

**Target (see design-system.md "Token source of truth + production delivery"):**
1. **`design/tokens.css` is the one source.** Circulation: `include_str!("../../design/tokens.css")`
   composed into each page's inline `<style>` at render — collapses the six duplicated `:root` blocks.
   Per-page **inlined** CSS is deliberate (best Lighthouse: no render-blocking sheet, no unused-CSS).
2. **Fonts become a hashed static asset.** `include_bytes!` the Source Serif 4 woff2 (from
   `~/Developer/seanfloyd.dev/public/fonts/source-serif-4-latin.2a24bad4.woff2`), serve at
   `/assets/fonts/source-serif-4.{sha}.woff2` with `Cache-Control: public, max-age=31536000, immutable`
   (compute the short SHA of the bytes at startup, build the route + `@font-face src` to match). **New
   static route** — none exists today. Do NOT base64-inline fonts in production (the mockups only do
   that for self-containment).
3. **Newsroom:** point `{{STYLES}}` at `tokens.css` + the digest component CSS; keep `resolve_css_variables`
   for email; the digest **web** view references the same hashed font URL so all surfaces share one
   cached download; email keeps the Georgia fallback.
4. **Parity canary test:** assert both consumers resolve identical token hex (self-expiring).

## 3. Per-surface port

Each: current handler/template → target mockup → the specific work. All chrome pages share the top
bar + footer + toggle from the contract in design-system.md — build those once as shared helpers.

### 3a. Index / home — `GET /` (`handlers.rs:56`, `templates/index.rs`)
- **Current:** one query `SELECT date, COALESCE(preheader,'') FROM digests ORDER BY date DESC` — **all
  rows every request** (200 today), rendered as **collapsible month accordions** (first month open).
- **Target (`chrome_v12`):** chrome top bar + masthead + a search box + a Recent/This year/All
  segmented control + an **issue-numbered running-order list** with month dividers + a subscribe card
  + the shared footer.
- **Work:** rewrite `render_index` to the new layout. **MVP reuses the existing all-rows query** — 200
  text rows is an acceptable page (no new endpoint needed). The segment and any calendar are **client-side
  filters over the already-loaded rows** (no API). See §5 for the index decision.
- **MVP vs mockup gap:** the mockup shows a per-issue **source count + bias-spread bar**, but the current
  query returns only `date + preheader`. So the MVP list row = date + preheader (as today, restyled);
  the per-issue source-count/bias needs a query addition (count + L/C/R split of `shown_narratives` per
  `run_id`) — an enhancement, not MVP. Say which you want before building the row.

### 3b. Digest-view — `GET /{date}` (`handlers.rs:529`, chrome injected by `templates/digest.rs`)
- **Current:** stored HTML blob (the *old* design) from `newsroom/render.py`, with circulation injecting
  a top util-row (`digest_nav_html`), skip-link, `<main>`, and footer around it.
- **Target (`digest_v1`):** the dispatch digest body + the chrome top bar (nav + translate + toggle) +
  a no-flash head script. Two parts:
  - **(newsroom — the big one):** port the dispatch design into `newsroom/templates/digest.css` + the
    digest HTML template — masthead, dateline, AI-notice, deck, Story/Brief tiers, the bias-glyph +
    3-tier `<details>` sources block, all per design-system.md's component specs. Preserve the email
    dual-render (`web-only`/`email-only`, table-based masthead for Outlook).
  - **(circulation `digest.rs`):** replace `digest_nav_html`'s util-row with the chrome top bar (nav
    `← Archive · Sources · Threads · Stats` + translate pill + theme toggle); inject the no-flash script;
    reconcile the injected footer. The digest keeps its own `--bg`/`--hair` tokens + sharp severity; the
    top bar is styled from the digest's tokens.

### 3c. Thread detail — `GET /thread/{id}` (`thread.rs:251`, `templates/thread.rs`)
- **Current:** `fetch_thread` (header + installments⋈runs⋈digests → per-installment `delta`), rendered as
  a plain reverse-chron list with a `← All threads` back-link.
- **Target (`thread_detail_v1`):** the evolving-story tracker — status marker (live-dot/hollow),
  "story so far" standfirst, a **"Still watching" open-questions ledger**, and a timeline of dated
  deltas.
- **Work + data reality:**
  - Timeline (installments + top-3 `whats_new` deltas): **EXISTS.**
  - **Still-watching ledger** — `thread_questions` (open/resolved, 445 rows) **exists but is never
    queried.** Add `fetch_thread_questions` (a query only; data is there).
  - **Story-so-far** — **no thread-level field exists.** MVP: reuse the latest installment's delta as
    the standfirst. Enhancement: a synthesized overview field (newsroom work).
  - **Per-fact source links — WORTH BUILDING (deterministic, no LLM/token spend).** The `A185` ids in
    `content.whats_new` are opaque per-run synthetic ids that don't key into `fetched_articles`, so
    circulation can't resolve them *retroactively* and current code strips them (`thread.rs:175-227`).
    BUT the run's `article_index.json` (A-id → `{url, source_id, name, bias}`) **is available at
    synthesis/record time** — so resolve the citations **when the installment is written** (in
    `newsroom`, `record_installment`/`thread_synthesis`) and persist the resolved `{url, outlet, bias}`
    with each fact (embed in the `content` JSON or a `thread_installment_sources` table). Circulation
    then renders the digest-style numbered source links. **Caveat: going-forward only — `article_index.json`
    is a single file overwritten every run (no per-run history), so the 269 existing installments can't
    be backfilled.** Fine in practice: threads are ongoing, so active threads accrue source-linked
    *recent* deltas (what readers see) within days; old deltas stay link-less. MVP the thread page
    without links, add this in the same newsroom pass as the digest port.
  - **OG/Twitter tags:** none today — add them (title = label, description = story-so-far).
  - **Nav:** `← Archive · Sources · Threads · Stats` (Threads is the back-to-index path; the redundant
    `← All threads` row is removed).

### 3d. Threads index — `GET /threads` (`thread.rs:270`)
- **Current:** `fetch_thread_summaries` (id/label/status/updated_at). **Target (`threads_v1`):**
  status-grouped list, filled-accent live-dot (ongoing) / hollow shape-coded (dormant/closed).
  **Work:** restyle to chrome + status markers. Data exists.

### 3e. Sources — `GET /sources` (`handlers.rs:339`)
- **Current:** `sources.json` (`include_str!`) + a hard-coded MBFC-slug map. **Target (`sources_v2`):**
  single continuous **bias-spectrum bar** (balance) + **factuality ordinal meter** (not green/amber) +
  source list. **Work:** restyle to chrome. Data (sources.json) exists. Add the honest caption: "all
  sides" is bounded by a **factuality floor** — the real catalog is `{lean-left, center, lean-right}`
  only (no far-/proper-left/right), excluded on quality, not curation.

### 3f. Stats — `GET /stats` (`stats.rs:317`), `GET /stats.json` (`stats.rs:252`)
- **Current:** exactly five metrics (`StatsData`, `stats.rs:50-57`): source-health success, usage-by-tier,
  recent-runs cost, dedup count+avg-similarity, never-selected.
- **Target (`stats_v2`):** balance hero, redundant-coded RAG health, honest "Planned" roadmap tiles.
- **Work — two tracks:** (1) restyle the current five to chrome; wire the **period toggle** (7d/30d/all)
  the mockup shows — needs a `?period=` param + a `WHERE run_at >= …` window (no period filtering exists
  today). (2) The **metrics rethink is a separate,
  higher-value engineering track** (`docs/2026-07-04-stats-metrics-backlog.md`): CUT dedup avg-similarity
  (keep count), REFRAME usage→concentration (HHI/Gini) + never-selected→coverage%, ADD bias-balance
  (Shannon/JSD, cheap — a `sources.json` bias join), catalog coverage, cost-per-subscriber/story. Some
  ADDs need new instrumentation (publish→surface latency = a new `shown_narratives` column; dedup F1 =
  a labelled set; deliverability = Resend webhooks) — mark those "Planned" in roadmap tiles, don't fake them.

### 3g. Feedback — `GET /feedback` (`handlers.rs:910`)
- **Current:** a GET vote handler that opens the DB **READ_WRITE** and INSERTs into `story_feedback`,
  with a mailto fallback when params are missing.
- **Target (`feedback_v1`):** no form — a warm mailto CTA + "especially useful to hear" list + the
  standard nav; content capped at a narrow measure, left-aligned in full-width chrome.
- **Work:** restyle to chrome. **The Yes/No vote was removed product-wide** — drop the vote-write path;
  `/feedback` becomes mailto-only (read-only). Keep the route (already-sent emails link to it).

## 4. Interaction matrix — every interaction → backing → status

| Interaction | Surface | Backed by | Status |
|---|---|---|---|
| Theme toggle (3-state, localStorage, no-flash) | all | client JS + inline `<head>` script | **BUILD** (new JS; no API) — spec in design-system.md |
| Translate pill | all | `GET /{date}/translate`, `/today/translate`, `?lang=` | **EXISTS** — wire href |
| Nav links (Archive/Sources/Threads/Stats) | all | `/`, `/sources`, `/threads`, `/stats` | **EXISTS** |
| Footer: RSS | all | `GET /feed.xml` | **EXISTS** |
| Footer: Privacy | all | `GET /privacy` | **EXISTS** |
| Footer: GitHub / seanfloyd.dev | all | external URLs | **EXISTS** (config the URLs) |
| Subscribe | index, digest | `POST /subscribe` | **EXISTS** |
| Search submit | index | `GET /search?q=` (FTS5, LIMIT 50) | **EXISTS** — restyle to `search_v1` |
| Search result → digest | search | links to `/{date}` | **EXISTS** |
| Index segment (Recent/This year/All) + date-jump | index | client-side over loaded rows (demoed in `chrome_v12`) | **BUILD** (client JS; no API) |
| Search → specific story anchor | search | digest needs stable per-heading `id`s | **BUILD** (enhancement) |
| Search result enrichment (source, snippet) | search | query + FTS `snippet()`; source via `shown_narratives.source_id` join | **BUILD** (enhancement) |
| Search-as-you-type (JSON) | index | new JSON endpoint | **BUILD** (future) |
| Archive list | index | `GET /` (all rows) | **EXISTS** — reuse |
| Segment Recent/This year/All | index | client-side filter of loaded rows | **BUILD** (client JS; no API for MVP) |
| Calendar date-jump | index | client-side (dates from loaded rows) for MVP, else `/dates.json` | **BUILD** (v2) |
| "Load more" / pagination | index | `GET /archive.json?before=&limit=` | **BUILD** (deferred — not needed at 200 issues) |
| Per-issue source count + bias bar | index | query addition: count/bias of `shown_narratives` per `run_id` | **BUILD** (enhancement) |
| Thread timeline + deltas | thread detail | `fetch_thread` | **EXISTS** |
| Thread "Still watching" ledger | thread detail | `thread_questions` (add query) | **BUILD** (query only; data exists) |
| Thread "story so far" | thread detail | latest delta (MVP) / new synthesis field | **MVP EXISTS / BUILD** |
| Thread per-fact source links | thread detail | `A185`→URL mapping (does not exist) | **BUILD** (new persistence; MVP ships without) |
| Thread OG/Twitter tags | thread detail | head tags | **BUILD** (small) |
| Feedback mailto | feedback | `mailto:` | **EXISTS** — drop vote-write |
| Sources page | sources | `sources.json` | **EXISTS** |
| Stats (current 5) | stats | existing queries | **EXISTS** — restyle |
| Stats period toggle (7d / 30d / all) | stats | query needs a `?period=` window param + `WHERE run_at >= …` | **BUILD** (no period filtering today) |
| Stats (backlog metrics) | stats | new queries / columns / webhooks | **BUILD** (separate track) |
| Fonts | all | `/assets/fonts/*.woff2` | **BUILD** (new static route) |

## 5. Open decisions — recommendations + what needs Sean's call

- **Index archive (~200 issues, growing daily).** RECOMMENDATION: MVP = server-render **all** issues in
  the new running-order list (month dividers), with the Recent/This year/All segment and a calendar
  date-jump implemented **client-side** over the loaded rows (no new endpoint). Defer `/archive.json`
  load-more until issue count makes the full page heavy (not yet). **Not** month accordions (research
  leans editorial-list) and **not** numbered pagination (clunky for a reverse-chron feed). *Confirm the
  MVP-client-side vs build-the-JSON-endpoint-now call.*
- **Search.** RECOMMENDATION: MVP = restyle the existing full-page `GET /search` into the new chrome
  (rows: date · tier · headline · → issue). Enrichment (source/snippet), story-anchor deep-links, and
  live search-as-you-type are enhancements, each needing the "BUILD" work above.
- **Thread per-fact sources — BUILD it (revised).** Deterministic, no token spend, and it restores the
  credibility mechanism. Resolve A-ids at installment-write time (article_index is in hand then) and
  persist `{url, outlet, bias}`; circulation renders numbered links. Going-forward only (article_index
  is overwritten per run → no backfill), which is fine since threads are ongoing. Do it in the newsroom
  port pass. (Earlier "defer" was too hasty — the resolution is cheap *at write time*, only impossible
  *retroactively*.)
- **Thread story-so-far:** latest-delta (MVP, free) vs a synthesized overview field (newsroom work).
- **Stats metrics rethink:** a separate track (backlog doc); the restyle can ship on the current five.
- **Digest footer:** stays reader/email-focused (Past digests · Sources · Threads · Privacy · Subscribe
  · translate · feedback) — NOT the app-chrome footer. Deliberate (it's the *document's* footer).

## 6. Assumptions (stated so they can be checked)

- ~200 published issues over 2025-12-05 → 2026-07-03 (7 months), growing ~1/day. The archive will need
  server-side pagination *eventually*, not now.
- Source Serif 4 woff2 lives at `~/Developer/seanfloyd.dev/public/fonts/` (weights 380–640 variable).
- Two token families are intentional: digest (`--bg`/`--hair`, solid hairlines, sharp) vs chrome
  (`--paper`/`--line` alpha, soft 6/8px radius). Documented divergence, not drift.
- Email constraints hold: Outlook Word-engine (table-based masthead, no flex), Gmail/Outlook strip
  `<details>` (so tier-3 sources are `web-only`), Georgia display fallback, `resolve_css_variables`
  literal-hex inlining at send time.
- `story_feedback` votes are removed product-wide → `/feedback` is mailto-only.
- Browser target: evergreen only (Baseline 2023+; `color-mix`, `text-wrap:pretty` degrade gracefully);
  no legacy support required.
- Verification is real, not asserted: `make lighthouse` (100 gate), `make a11y`, `node cssdiff.js`
  (chrome consistency), and for the digest a `--dry-run`/`--no-email` render inspected by eye + the
  email surface checked.

## 7. Build order (each step verifiable before the next)

1. **Foundation (§2):** `tokens.css` `include_str!` + the `/assets` hashed-font route + newsroom
   `{{STYLES}}` token wiring + the parity canary. Nothing renders right until this lands.
2. **Chrome pages (§3a,d–g):** build the shared top-bar/footer/toggle helpers once, then restyle index,
   sources, stats (current five), threads, thread-detail, feedback to the mockups. After each: `make
   lighthouse` (100) + `node cssdiff.js` (no drift) + `make a11y`.
3. **Digest (§3b):** port the dispatch design into newsroom (`digest.css` + template) + update
   circulation `digest.rs` chrome injection. Verify with a dry-run render (inspect output) + email
   dual-render (Apple Mail / Gmail / Outlook fallback).
4. **Interaction enhancements (§4, prioritized):** thread `thread_questions` ledger query; index
   per-issue metadata + client-side segment/calendar; search restyle.
5. **Deferred / needs-decision:** `/archive.json` load-more, calendar `/dates.json`, thread source
   resolution, stats backlog metrics — each its own scoped task.

## 8. Verification gates (already built)

- `make lighthouse` — a11y/BP/SEO = 100, pre-deploy gate. Retarget at circulation with
  `bin/lighthouse --url http://localhost:8081/{route}` once pages are ported.
- `make a11y` — fast structural invariants (one h1, `<main>`, lang, title, img alt, accessible names,
  no positive tabindex). Per-commit-suitable; add to `bin/ci` at port time.
- `node scratch/chrome-mockups/cssdiff.js` — cross-page chrome consistency; keep green.
- Parity canary (to be written in §2) — token hex identical across circulation + newsroom.
