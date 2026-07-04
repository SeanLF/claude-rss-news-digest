# Spec: /archive.json endpoint + index against real data

**Status:** approved-by-delegation (Sean AFK: "make the informed decision, we can revert later").
**Goal:** validate the redesigned index against **real DB data** instead of mockup placeholders, by
building the first real port slice — a paginated archive data source, a JSON endpoint, and the index
rewritten to render it (server-rendered first page + load-more). First implementation step of the
port (`docs/2026-07-04-port-implementation-handover.md`).

## Decisions (made across the lenses; reversible)

- **Full per-issue row** — date, derived issue number, preheader, source count, bias split (L/C/R),
  must/should counts. The point of "real data" is validating the per-issue metadata the mockup fakes.
- **Slice = endpoint + server-rendered page 1 + load-more + the font route** (so it looks right in
  Source Serif 4). Segment/calendar filters and the token-foundation refactor across all pages are a
  clean follow-up, **out of scope here**.
- **Implement now** (a real, reviewable change beats a plan; revert is the safety net).
- **Progressive enhancement**: page 1 is server-rendered (fast, no-JS-safe); load-more is an
  `<a href="/?before=…">` that JS intercepts to fetch `/archive.json` and append. Works without JS
  (navigates to the next server-rendered page), better with JS (appends).

## Data design (validated against `data/digest.db`)

**`fetch_archive(db_path, before: Option<&str>, limit: i64) -> Result<Page, (StatusCode, String)>`**
mirrors `stats::fetch_stats_data` conventions (READ_ONLY conn, `prepare`/`query_map`/`log_row_error`,
`(StatusCode, String)` errors). Two queries:

1. **Base rows** (cursor-paginated, newest first), fetch `limit + 1` to detect "has more":
   ```sql
   SELECT d.date, COALESCE(d.preheader,'') AS preheader, d.run_id,
          (SELECT COUNT(*) FROM digests d2 WHERE d2.date <= d.date) AS issue_no,
          (SELECT COUNT(DISTINCT sn.headline) FROM shown_narratives sn
             WHERE sn.run_id = d.run_id AND sn.tier='must_know')   AS must,
          (SELECT COUNT(DISTINCT sn.headline) FROM shown_narratives sn
             WHERE sn.run_id = d.run_id AND sn.tier='should_know') AS should
   FROM digests d
   WHERE (?1 IS NULL OR d.date < ?1)      -- cursor: issues strictly before `before`
   ORDER BY d.date DESC
   LIMIT ?2                                -- bind limit+1
   ```
   - **Issue number** = ascending rank by date (oldest = #1). Derived (no stored column). The
     correlated `COUNT(*) … date <= d.date` is simpler than a window fn and needs no CTE; validated
     (newest issue = 200). (`ROW_NUMBER() OVER (ORDER BY date)` is available if preferred later.)
   - `run_id` is nullable — **5 legacy digests have NULL run_id.** Keep them (don't hide issues): their
     `must/should` come back 0 and their source/bias aggregation is empty. Issue numbering stays
     consistent because the rank counts all digests.
2. **Per-run sources for the page** — one grouped query scoped to the page's run_ids:
   ```sql
   SELECT run_id, source_id FROM shown_narratives
   WHERE run_id IN (…page run_ids…) GROUP BY run_id, source_id
   ```
   In Rust, map each `source_id` → bias via `sources.json` (`include_str!("../sources.json")`, per the
   `handlers::sources` pattern), bucket to L/C/R, and count. `source_count` = distinct sources per run.
   - **Bias buckets:** L = {far-left,left,lean-left}, C = {center}, R = {lean-right,right,far-right}.
     Real catalog is only `{lean-left, center, lean-right}` (the factuality-floor reality) — the bar
     reflects that honestly.
   - **Null/unknown source_id** (1 row in DB) or a source not in `sources.json` → an "unknown" bucket,
     **never a panic**. Unknown is excluded from the L/C/R bar (or shown faint); it must not crash.

**Page struct:** `{ issues: Vec<IssueRow>, has_more: bool, next_before: Option<String> }`.
`IssueRow`: `{ date, issue_no: i64, preheader, source_count: i64, bias_l/bias_c/bias_r: i64,
must: i64, should: i64 }`, `#[derive(Serialize)]` (like `HealthResponse`). `next_before` = the oldest
`date` in the page when `has_more`, else `None`.

## Endpoint — `GET /archive.json?before=&limit=`

- Query params (`#[derive(Deserialize)] struct ArchiveQuery { before: Option<String>, limit: Option<i64> }`),
  `limit` clamped to `1..=100` (default 30) so it can't be abused.
- Returns `axum::Json(Page)` (serde). Errors `(StatusCode, String)`.
- Route `.route("/archive.json", get(handlers::archive_json))` — no collision with `/{date}` (proven by
  `/stats.json`). Add `pub const ARCHIVE_JSON` to the `routes` module for consistency.

## Font route — `GET /assets/fonts/source-serif-4.{hash}.woff2`

- `const SOURCE_SERIF: &[u8] = include_bytes!("../assets/source-serif-4-latin.woff2")` (copy the woff2
  into `circulation/assets/`). Compute a short SHA-256 hex of the bytes **once at startup**, store the
  filename on `AppState` (or a `OnceLock`); the `@font-face src` in the index CSS points at it.
- Handler mirrors `og_image`: `(StatusCode, [("content-type","font/woff2"),
  ("cache-control","public, max-age=31536000, immutable")], SOURCE_SERIF)`. Route
  `/assets/fonts/{file}` with `Path(file)`, 404 on an unknown filename (include_bytes needs a
  compile-time path, so match the known hashed name).
- Two-segment route — cannot collide with `/{date}`.

## Index rewrite — `render_index` → the new design, real data

- Keep `pub fn render_index(p: &IndexParams) -> String`; **extend `IndexParams`** with the rendered
  issue-list HTML (`issue_rows: &str`), the load-more link (`load_more: &str`, empty when no more), the
  font href, and the toolbar bits. `handlers::index` builds these from `fetch_archive(None, PAGE)`.
- Port `chrome_v12`'s markup + CSS into the `format!` (chrome top bar + masthead + issue-numbered
  running-order list + subscribe card + `.site-foot`). CSS inline in the `<style>` block (the
  token-foundation `include_str!` refactor across all pages is a **separate** follow-up; this slice
  inlines, matching the current file). `@font-face` uses the hashed `/assets/fonts/…` URL.
- **Each issue row** renders the real data: `#{issue_no}` · `{date}` · `{preheader}` · a bias-spread
  bar (`bias_l/c/r` widths) + `{source_count} sources`. Carry `data-year` (from date) for the future
  client-side segment.
- **Load-more**: `<a class="loadmore" href="/?before={next_before}">Load more</a>` when `has_more`;
  a small inline `<script>` intercepts the click, `fetch('/archive.json?before='+d+'&limit=30')`,
  appends rendered rows, and updates the link's `href`/removes it at the end. `GET /?before=` is
  supported by `handlers::index` reading an optional `before` query so the no-JS path server-renders
  the next page.
- Update the two `index.rs` tests (they assert `search` action + og tags) to match the rewrite.

## Testing (TDD — temp-dir sqlite fixture)

No `[dev-dependencies]`, no `tests/` dir, no `:memory:` — copy `state_with_digests` (handlers.rs:717):
a unique temp-file DB, `CREATE TABLE` + seed, call the `pub fn` directly. Tests, failing-first:

1. `fetch_archive` returns rows newest-first with correct `issue_no` (ascending rank), `must/should`,
   `source_count`, and L/C/R split for seeded `digests`+`shown_narratives` (+ a seeded `sources.json`
   bias — or assert against the real symlinked file for known ids).
2. Cursor: `before` excludes issues ≥ the cursor; `has_more`/`next_before` correct at a page boundary.
3. Edge: a digest with NULL `run_id` still appears (0 must/should, empty bias); a NULL/unknown
   `source_id` doesn't panic and lands in "unknown".
4. `archive_json` handler returns 200 + valid JSON with the expected keys.
5. Font handler returns 200, `content-type: font/woff2`, `cache-control` contains `max-age`, and the
   body starts with the `wOF2` magic bytes (clone `og_image_serves_png_bytes_with_long_cache_header`).

Then `make ci` (cargo fmt/clippy/test), run the server, `curl /archive.json` (eyeball real data),
screenshot the index + `make lighthouse`/`make a11y` on the rendered page.

## Research findings (2026-07-04) — corrected architecture + a11y/HIG (cited)

Two research passes (prior art + architecture) grounded what was previously assumed. **Net: the
load-more UX pattern holds, but the transport changes from JSON to HTML-over-the-wire.**

**UX/HIG — Load more > numbered pagination > infinite scroll (avoid).**
- Infinite scroll is **disqualified** for an archive (a *findability* task, not a discovery feed):
  footer-unreachable, place-loss on back-nav, keyboard/SR-hostile, WCAG bypass-block issues
  [GOV.UK Pagination — "avoid infinite scroll"; NN/g Infinite Scrolling; Baymard/Smashing; Deque on
  role=feed]. Load-more tested best (more items viewed + more attention/item, footer reachable)
  [Baymard]. Numbered pagination is the a11y baseline (each page a URL) but perceived slow.
- **A11y requirements the load-more MUST meet** (non-negotiable): (1) **URL-addressable state**
  (`?before=<date>`) so back-button/bookmark/deep-link restore position; (2) **focus moves to the
  first newly-appended row** after Load more (most-missed); (3) **`aria-live="polite"`** announcing
  "X of N loaded"; (4) a real `<button>`/`<a>`, keyboard-operable, labelled with remaining count, **no
  scroll-auto-load**; (5) a **plain paginated fallback** (older/newer links) for no-JS/crawlers.
- **Date-jump:** worthwhile *because* issues map 1:1 to calendar days, but per NN/g Date-Input:
  **disable/grey-out empty dates, bound to `[first…today]`, no future, no ranges**; for a ~200-day span
  a free calendar is borderline — a **month/year quick-jump or a calendar limited to real issue-dates**
  is safer than a bare `<input type=date>`. (⇒ the v2 `/dates.json` becomes MVP-worthy sooner.)
- **Prior art:** editorial/CMS archives (Substack, Ghost, Guardian) use **reverse-chron dated rows +
  older/newer or load-more**, not infinite feeds — copy that.

**Architecture — HTML-over-the-wire, NOT a JSON API.**
- A JSON endpoint duplicates the row markup (Rust template + client JS renderer) for a list **only this
  server consumes** — the "JSON API you don't need" anti-pattern [htmx *Hypermedia APIs vs Data APIs*].
  Data APIs are justified only for third-party consumers / versioning / a real SPA — none apply.
- **Chosen:** the endpoint returns rendered `<li>` **HTML fragments from the same template** the full
  page uses (single source of markup, no drift), appended by **~30 lines of vanilla `fetch` +
  `insertAdjacentHTML`** over a degradable `<a href="/archive?before=…">`. That `<a>` **is** the
  URL-addressable no-JS fallback the a11y research demands — one mechanism, both wins [Hotwire; htmx
  *Hypermedia-Driven Applications*].
- **Not htmx/Turbo/Stimulus/GraphQL:** htmx (~14kb) pays off at ~5-6 such interactions; this is *one*,
  on a deliberately no-build/no-framework stack → vanilla wins ("every dependency is liability").
  Revisit htmx if a 2nd/3rd HTML-over-the-wire behaviour appears.

**Bias bar a11y (design-skill catch):** the per-row L/C/R bar is **colour-only** → WCAG 1.4.1. Each row
must carry the split as text/`aria-label` (like the digest's "5 left · 4 center · 2 right"), not just
"N sources".

**Code corrected (2026-07-04):** `circulation/src/archive.rs` is now the **data layer only** —
`fetch_archive` (validated) + `bias_map` + tests; the JSON wrapper (`archive_json`, `ArchiveQuery`,
`Serialize`, the `/archive.json` route) was **removed** (wrong transport). `#![allow(dead_code)]` until
the index rewrite (HTML-over-the-wire) consumes it. `cargo test` 4/4, clippy clean.

## Progress (2026-07-04)

- ✅ **Phase 1+2 — the endpoint — DONE, verified against real data.** `circulation/src/archive.rs`
  (`IssueRow`, `Page`, `fetch_archive`, `bias_map`, `archive_json`) + route in `main.rs`. 4 TDD tests
  pass (issue-number rank, counts + bias split, cursor/has_more, null-run_id/null-source_id no-panic);
  `cargo clippy` clean; full suite 118/118 green. Live `GET /archive.json?limit=2` on `data/digest.db`
  returns correct real values (issue #200, source_count 24, bias 10/12/2, must 5/should 12, cursor).
  Uncommitted (new module + 2-line `main.rs` addition; keeps clear of Sean's WIP in `handlers.rs`).
- ⏭️ **Phase 3 (index UI rewrite) + the font route — DEFERRED to a fresh session.** Deliberate call:
  the index rewrite is a large edit to `render_index`'s raw-string `format!` (≈285 lines of CSS + markup,
  every literal brace doubled) — exactly the kind of work whose quality degrades at the tail of a very
  long session, and which the project's own learning says is ~3× cheaper/cleaner in fresh context. The
  endpoint (the risk-bearing data logic) is the part best done + verified here; the UI port is
  mechanical and should be done fresh, consuming the now-proven endpoint. Font route (`include_bytes!`
  woff2 + hashed path) belongs with the index slice (it's only consumed there).

## Implementation plan (phased; each phase green before the next)

1. **Data + tests:** copy the woff2 into `circulation/assets/`; add `IssueRow`/`Page`, `fetch_archive`,
   and its unit tests (TDD). Add the `source_id→bias` helper. `cargo test` green.
2. **Endpoints:** `archive_json` handler + route + test; font handler + route + test. `cargo test` green.
3. **Index rewrite:** extend `IndexParams`, rewrite `render_index` to the new design, update
   `handlers::index` to call `fetch_archive` + build rows + support `?before=`. Fix the index tests.
   `cargo test` + `cargo build` green.
4. **Verify against real data:** run circulation on `data/digest.db`, `curl` the endpoint, screenshot
   the index (light+dark), `make lighthouse` + `make a11y` on the served page.

## Out of scope (documented follow-ups)

- The token-foundation refactor (single `tokens.css` `include_str!` across all six chrome pages) — this
  slice inlines the index's CSS, matching the current file.
- The other pages' ports (sources/stats/threads/thread-detail/feedback/digest).
- Segment (Recent/This year/All) + calendar **server support** (`?year=`, `/dates.json`) — the client
  hooks (`data-year`, the date input) are laid in, wiring is the follow-up.
- Search restyle; thread source resolution; stats metrics.

## Error handling & reversibility

- Every request-path DB op uses `?`-propagated `(StatusCode, String)`; no `unwrap` on request paths;
  `log_row_error` drops a bad row rather than failing the page.
- The whole slice is additive (new endpoint, new route, an index rewrite) on a branch — fully
  revertible. The digest/email path is untouched.

## Interaction model (settled 2026-07-04)

Settles how the three index affordances — the **segment** (Recent / This year / All), the **load-more**,
and the **date-jump** — compose. Reconciles the prior contradiction: `design-system.md` §"Index
interactions" said *MVP = client-filter a fully server-rendered list, load-more deferred*, while this
spec *built* the `/archive` load-more endpoint. Settled answer: **every segment is a server-scoped query;
there is no client-side filtering at all.** Delegated call (Sean AFK), reversible.

**Guiding constraint that forces the shape:** client-filtering a *partially-loaded* list is broken — if
only 30 of ~185 in-year rows are loaded, a client "This year" filter silently shows a truncated year.
The archive is ~200 issues today, +1/day (unbounded). So the only correct model is: the segment picks a
**server scope**, load-more pages *within* the active scope, and switching segment **resets** the list
with a fresh scoped fetch. One code path, no partial-list bugs, no client filter.

### (a) What each segment does — server scope, not client filter

| Segment | Server scope | Load-more? | Why |
|---|---|---|---|
| **All** (default) | reverse-chron, cursor-paginated (`before`, 30/page) | **Yes** | The archive is unbounded (grows daily); paging back to issue #1 is the one place load-more earns its keep. This is the default server render (see no-JS below). |
| **Recent** | newest **15**, single fetch | No | A deliberately capped "top of the pile" quick-look. Bounded → one fetch, no paging (paging *within* "recent" is a category error). |
| **This year** | all issues in the current calendar year (`year=YYYY`), single fetch | No | A calendar year is bounded (≤366; ~185 today ≈ 74 KB of `<li>`). One fetch returns the whole year — never a partial-list filter. |

**Default = All, not Recent** (resolves the source doc's Recent-listed-first-vs-load-more-lives-in-All
tension). Forced by no-JS correctness: the default server render must carry the paginating `<a>` fallback
so every issue is reachable without JS; that pageable reverse-chron view *is* All. Recent and This year
are JS-enhanced narrowings over it. (Recent's newest-15 is provably a subset of All's first page, so it
*could* be a safe client-slice — but for a single uniform rule we fetch it too; 15 rows is negligible.)

*Rejected: load-all-then-client-filter.* Even at 200 rows it forecloses the unbounded future the endpoint
was built for, and "This year" over a partially-loaded All is exactly the broken case. Server scope is
the same effort and correct at any size.

### (b) Endpoint contract + URL state

**Fragment endpoint (built; one param added):** `GET /archive` returns an **HTML `<li>` fragment, rows
only** (no metadata — HTML-over-the-wire; client detects end by row count, see (c)).

| Request | Returns | Used by |
|---|---|---|
| `GET /archive?limit=30` | newest 30 `<li>` | (rarely direct; All page-1 is server-rendered inline) |
| `GET /archive?before=<date>&limit=30` | 30 `<li>` **strictly older than** `<date>` | All load-more (append) |
| `GET /archive?limit=15` | newest 15 `<li>` | Recent segment |
| `GET /archive?year=2026` | **all** 2026 `<li>`, newest-first | This year segment |

Change to the built code: add `year: Option<i64>` to `ArchiveQuery`; when set, the SQL swaps the cursor
predicate for `strftime('%Y', d.date) = ?year` and drops the `LIMIT` (a year is bounded). `before` and
`year` are mutually exclusive (year wins). `limit` stays clamped `1..=100`. **Also add `data-date="{date}"`
to the `<li>` in `row_html`** (currently only `data-year`) so JS can read the new cursor after an append
without parsing the `<a href>`. Optional 1-line clean-up: when `!has_more`, `archive_fragment` appends an
HTML comment `<!--end-->` so the client removes the button deterministically (else it costs one extra
empty fetch at an exact-30-multiple boundary — acceptable fallback).

**URL state on the main page** (`handlers::index` reads optional query; required by the a11y research for
back/bookmark/deep-link):

- `GET /` → default All: server-renders newest 30 + the load-more `<a>`. Segment control shows All active.
- `GET /?before=<date>` → a **discrete page**: the 30 issues strictly older than `<date>`, in full page
  chrome, with the load-more `<a href="/?before=<oldest-on-this-page>">` (and a "← Newer / to top" link
  back to `/`). This is the no-JS pagination baseline — each page a bookmarkable URL (research: "numbered
  pagination is the a11y baseline, each page a URL"). Same `before` semantics as the fragment (strictly
  older, 30) — one meaning everywhere.
- `GET /?year=YYYY` → the This year view server-rendered (deep-link/bookmark to a year), no load-more.

*Restore-position honesty:* on JS load-more, `history.replaceState('/?before=<cursor-just-fetched>')`
tracks depth so back/bookmark land at the right discrete page. Reload renders that **discrete page**, not
the byte-identical accumulation of every row scrolled — the a11y requirement (URL-addressable, back works,
position ≈ restored) is met; full accumulate-on-reload (a `date >=` range render) is an explicit v2, not
worth the second query shape now.

### (c) Load-more a11y wiring (non-negotiables from the research)

- **Real `<a class="loadmore" href="/?before=<date>">`** — keyboard-operable, and with JS off it
  *navigates* to the next server-rendered discrete page. One mechanism = both the enhancement and the
  no-JS/crawler fallback.
- **Labelled with remaining count + `aria-live`:** an `aria-live="polite"` status node renders
  `Showing 30 of 200 issues` (server sets `data-total` = `SELECT COUNT(*) FROM digests`). Updated after
  every append; at the end it reads `All 200 issues loaded`.
- **Focus moves to the first newly-appended row** after each Load more (the most-missed requirement): set
  `tabindex="-1"` on that row's `<a>` and `.focus()` it, so SR/keyboard users land on the new content.
- **No scroll auto-load**, ever (archive = a findability task; infinite scroll is disqualified).
- On fetch error, leave the `<a>` in place (it still works as the no-JS navigation) — never strand the user.

### (d) Date-jump — bounded native input, graceful miss (MVP; NOT `/dates.json`)

Ship the **native `<input type="date">`** with `min="<first-issue-date>"` (issue #1's date) and
`max="<newest-issue-date>"` (no future). On `change`, `location.href = '/' + value`.

**Decision: do NOT build `/dates.json` now.** The research "leaned toward restricting to real issue-dates,"
but that lean doesn't survive contact with this data + the no-custom-widget rule already settled in
`design-system.md`:
1. Publication is **near-daily and contiguous** (~200 issues over ~200 days; gaps are rare outage days).
   The miss rate on a random in-range pick is low.
2. A native `<input type=date>` **cannot disable arbitrary interior dates** — HTML has no such attribute.
   Greying individual gap-days would require a **custom calendar widget**, which the design *explicitly
   rejected* ("a native picker *is* the calendar — no custom widget"). So `/dates.json` buys nothing the
   native control can express; `min`/`max` already block the out-of-range majority of bad input.
3. The right fix for the rare interior miss is a **soft 404**: `GET /{gap-date}` returns a "No issue on
   {date}" page linking the nearest prior issue + the archive — cheaper than `/dates.json` and a better
   landing than graying a day. (Revisit `/dates.json` only if publication becomes *sparse* — many gaps —
   which changes the regime.)

### (e) Segment × load-more interaction (unambiguous)

- **Switching a segment RESETS the list**: a fresh server fetch scoped to that segment, `list.innerHTML =`
  the returned fragment (replace, not append), reset the "X of N" announce, move focus to the first row,
  and `replaceState` the URL (`/` for All, `/?year=YYYY` for This year; Recent stays `/`).
- **Load-more respects scope by construction**: only **All** renders a load-more `<a>`. Recent and This
  year fetch their whole bounded scope, so there is no scoped-load-more ambiguity to resolve.
- **No preserved depth across switches**: All deep-loaded to 120 rows → switch to Recent (newest 15) →
  switch back to All → re-fetches page-1 (newest 30). Segment switch always resets. Simple and total.

### Vanilla-JS behaviour (~30 lines, pseudocode)

```js
const list   = document.querySelector('#issues');            // <ol id=issues data-total=200 data-year=2026>
const status = document.querySelector('#issue-status');      // <p aria-live=polite> "X of N"
const TOTAL  = Number(list.dataset.total);
const YEAR   = list.dataset.year;                            // current year, server-set
const shown  = () => list.querySelectorAll('li.issue').length;
const announce = (t) => status.textContent = t || `Showing ${shown()} of ${TOTAL} issues`;

function focusFirstNew(i) {                                   // move focus to first appended row
  const a = list.querySelectorAll('li.issue')[i]?.querySelector('a');
  if (a) { a.tabIndex = -1; a.focus(); }
}
async function loadMore(a) {                                  // All segment only
  const before = new URL(a.href, location).searchParams.get('before');
  const at = shown();
  const res = await fetch(`/archive?before=${before}&limit=30`);
  if (!res.ok) return;                                        // leave <a> as no-JS fallback
  list.insertAdjacentHTML('beforeend', (await res.text()).trim());
  const added = shown() - at;
  focusFirstNew(at); announce();
  if (added < 30) { a.remove(); announce(`All ${TOTAL} issues loaded`); }
  else {
    const cursor = list.querySelector('li.issue:last-child').dataset.date;  // needs data-date on <li>
    a.href = `/?before=${cursor}`;
    history.replaceState({}, '', `/?before=${cursor}`);
  }
}
async function selectSegment(scope) {                         // 'all' | 'recent' | 'year' — RESETS list
  const q = scope === 'year' ? `year=${YEAR}` : scope === 'recent' ? `limit=15` : `limit=30`;
  list.innerHTML = (await (await fetch(`/archive?${q}`)).text()).trim();
  history.replaceState({}, '', scope === 'year' ? `/?year=${YEAR}` : '/');
  rebuildLoadMore(scope);                                    // append a fresh <a.loadmore> only for 'all'
  focusFirstNew(0); announce();
}
document.addEventListener('click', e => {                    // delegation: survives <a> re-creation
  const a = e.target.closest('a.loadmore'); if (a) { e.preventDefault(); loadMore(a); }
});
document.querySelector('#segments').addEventListener('click', e => {
  const b = e.target.closest('button[data-scope]');
  if (b) { setActive(b); selectSegment(b.dataset.scope); }
});
document.querySelector('#jump').addEventListener('change', e => {
  if (e.target.value) location.href = '/' + e.target.value; // native date input, bounded min/max
});
```

**Server changes this model needs** (small, additive to the built endpoint): `year` param + query branch
in `fetch_archive`/`ArchiveQuery`; `data-date` on the `<li>` in `row_html`; `handlers::index` reads
optional `?before`/`?year` and passes `data-total` (a `COUNT(*)`); the soft-404 `/{gap-date}` page. All
revertible; the fragment transport and `fetch_archive`'s cursor path are unchanged.
