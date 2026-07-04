# Circulation states: error / empty / loading (design + decisions)

**Date:** 2026-07-04
**Scope:** The non-happy-path states for the circulation web surfaces (the redesign chrome).
Every state lives inside the **cross-page chrome contract** (top bar + masthead + footer +
tokens + serif/mono/sans roles) from `docs/design-system.md`. Copy is Sean's voice: truthful,
calm, no marketing-speak, **no cartoon illustrations** (design-skill "honest skips").

This doc **decides** each state — copy, markup, behaviour, a11y — and flags the small server
changes each one implies. It is the spec the templates port against.

---

## Grounding: what exists today (the gaps these states fill)

Verified against `circulation/src/`:

| Path | Today | Gap |
|---|---|---|
| `GET /{date}`, no issue | bare `(404, "Digest not found")` text | not in chrome, dead end |
| `GET /{bad-date}` | bare `(400, "Invalid date format")` text | not in chrome |
| unknown route | axum default empty 404 (no `.fallback`) | not in chrome |
| `GET /today`, empty DB | bare `(404, "No digests yet")` | not in chrome |
| `GET /` (index), empty DB | renders an empty `<ul>` | no calm empty state |
| `GET /search` no results | `<p class="search-hint">No results…</p>` (old tokens) | refine to chrome + voice |
| `POST /subscribe` ok | `303 → /?subscribed=1`, old `success-msg` div | restyle to chrome |
| `POST /subscribe` fail | bare `(500, "Subscription failed…")` text | not in chrome, **dead end, no retry** |
| `GET /archive?before=` | `<li>` fragment, **no end/more signal** | load-more can't detect the end |

**Server changes these states imply** (small, called out per-state, summarised at the end):
1. Add `.fallback(handlers::not_found)` + a shared chrome 404 renderer; route the `get_digest` /
   `today` miss-paths through it (status stays 404/400).
2. `subscribe` failure → `Redirect::to("/?subscribe_error=1")` instead of a bare 500 body, so the
   error lands back in the chrome index with the form intact to retry.
3. `archive_fragment` emits a trailing hidden **sentinel** carrying the next cursor when `has_more`,
   and omits it at the end — so the load-more JS can detect end-state deterministically.

---

## Shared components (used by several states)

### Notice bar (subscribe success / error; reusable)
Flat wire bar, **not** a rounded alert box. A `3px` left status border echoes the digest's
why-it-matters mark; `--panel` ground; hairline; **redundant coding** (glyph **and** word — survives
greyscale, covers CVD, satisfies WCAG 1.4.1). Status colour comes from the semantic axis
(`--ok` / `--warn` / `--accent`), **never** the brand accent for meaning.

```html
<!-- role varies: status (success, polite) | alert (error, assertive) -->
<p class="notice ok" role="status">
  <span class="ni" aria-hidden="true">✓</span>
  Subscribed. The next issue will land in your inbox.
</p>
```
```css
.notice{ display:flex; gap:8px; align-items:baseline;
  padding:12px 16px; margin:0 0 24px; background:var(--panel);
  border:1px solid var(--line); border-left:3px solid var(--muted);
  font:16px/1.5 var(--serif); color:var(--ink2); border-radius:0; }
.notice .ni{ font-family:var(--serif); font-weight:600; }
.notice.ok  { border-left-color:var(--ok);     } .notice.ok  .ni{ color:var(--ok-ink); }
.notice.warn{ border-left-color:var(--warn);   } .notice.warn .ni{ color:var(--warn-ink); }
.notice.bad { border-left-color:var(--accent); } .notice.bad .ni{ color:var(--accent-ink); }
```
Glyph set (the tiny semantic mark set, per ui-design-craft §1): `✓` ok · `▲` warn · `✕` bad · `·` info.

### Mono error eyebrow (404)
Wire-briefing metadata line — mono tracked caps, `--muted` (an error code is metadata, **not** an
alarm, so no accent/red): `<p class="eyebrow">ERROR · 404</p>`, mono 11 `.08em` caps.

---

## 1. Not found — `/{date}` with no issue · malformed date · unknown route

**Decision:** ONE chrome page (`render_not_found`) with two copy variants, served for all miss-paths.
Register `.fallback(handlers::not_found)` (variant `Unknown`); route `get_digest` /`today` misses
through it. Status: `404` for no-issue and unknown routes; a malformed date may keep `400` but renders
the **same** body (the distinction is copy, not a different page). No illustration; the mono `404`
eyebrow is the only "error" signalling and it's metadata-toned, not alarmed.

**Two variants** (truthful specificity — the index date-jump is the *designed* path into a NoIssue 404):
- `NoIssue { date }` — a well-formed date the reader jumped to that has no issue (future date, or a
  gap day).
- `Unknown` — an unknown route or malformed date.

**Copy**
- eyebrow (both): `ERROR · 404`
- NoIssue h1: `No issue for that date`
  body: `There's no issue dated {Fri 4 Jul 2026} — nothing was published that day, or the date is still ahead.`
- Unknown h1: `Not found`
  body: `That page doesn't exist, or it has moved.`
- both, orientation links (sans, underlined): `Read today's issue` (→ `/today`) · `Browse the archive` (→ `/`)

**Markup** (full chrome shell; `<main>` holds a short block, left-aligned to the column edge)
```html
<div class="wrap"><div class="col">
  <a class="skip" href="#main">Skip to content</a>
  <!-- standard top bar: ← Archive · Sources · Threads · Stats | 文A Translate ◐ theme -->
  <div class="topbar">…</div>
  <header class="mast">
    <p class="brandline">Sean's Daily <em>Digest</em></p>
    <p class="eyebrow">ERROR · 404</p>
    <h1>No issue for that date</h1>
  </header>
  <main id="main">
    <p class="lede">There's no issue dated Fri 4 Jul 2026 — nothing was published that day,
      or the date is still ahead.</p>
    <p class="nfnav"><a href="/today">Read today's issue</a>
      <span class="sep" aria-hidden="true">·</span>
      <a href="/">Browse the archive</a></p>
  </main>
  <footer class="site-foot">…</footer>
</div></div>
```
`.lede` = serif `--ink2`, body size, generous whitespace (one calm sentence, per the skill).
`.nfnav` links = sans 13, underlined (never colour-only), `--accent-ink` on hover.

**Behaviour**
- `get_digest`: valid-format date, no row → render NoIssue with **404**.
- malformed date → render Unknown (keep 400 if preferred; body identical).
- `.fallback` (any unmatched route) → render Unknown, **404**.
- `today` / `today_translate` empty-DB → **redirect to `/`** (the empty-archive state, §2) rather than a
  404 — "no digests yet" is an empty archive, not a not-found. (Decision: reserve 404 for genuine misses.)

**a11y**
- `<title>Not found — Sean's Daily Digest</title>`; exactly one `<h1>`; skip link present.
- Real `404` status code (SEO + AT + correctness), not a 200 soft-404.
- The `404` eyebrow is decorative-metadata, `--muted` (no colour-only meaning); links carry underline;
  focus outline `--accent`. Nothing here needs a live region (static server render).

---

## 2. Empty archive — index with no issues yet

**Decision:** When `digests.is_empty()`, the index keeps its masthead + subscribe affordance (subscribe
is exactly the right CTA when there's nothing to read yet) and replaces the running-order list with **one
calm serif line**. Hide the segmented control (nothing to filter) and the Load-more control.

**Copy**
- with subscriptions on:
  `No issues published yet. The first digest lands after the next morning run — subscribe below and it'll be in your inbox.`
- with subscriptions off:
  `No issues published yet. The first digest lands after the next morning run.`

**Markup**
```html
<main id="main">
  <!-- segmented control + date-jump omitted when empty -->
  <p class="empty">No issues published yet. The first digest lands after the next
     morning run — subscribe below and it'll be in your inbox.</p>
  <!-- subscribe form (unchanged happy-path component) stays below -->
</main>
```
```css
.empty{ font:18px/1.6 var(--serif); color:var(--muted); margin:8px 0 32px; max-width:60ch; }
```

**Behaviour** Pure server render. The list, month dividers, segment toggle, date-jump, and load-more all
render only when `!digests.is_empty()`. `/today` redirects here (§1) so the "no digests yet" path is a
single, coherent empty state.

**a11y** One `<p>`, no live region. Masthead `<h1>` still the page's single h1. No colour-only meaning.

---

## 3. Empty search — "no results for …" (+ landing state)

**Decision:** Refine the existing state into the chrome + voice. Two no-result sub-states: **no query**
(landing) and **query with zero hits**. Keep the mono `rescount` line pattern from `search_v1`; a zero
count reads as a calm mono line, not a blunt "0 results". Add one muted refine hint. Keep the standing
`note` describing what search covers. **No** fabricated "did you mean" / suggestions (we have no such
data — honest skip).

**Copy**
- Landing (no `q`): `<p class="note">Search matches wording in every published headline. Enter a term above.</p>`
- Zero results:
  count (mono): `No results for "volcano".`
  hint (sans muted): `Search matches headline wording — try a broader or differently-worded term.`
- Standing note (all states): `Results match headlines across every past issue; each opens that day's digest.`

**Markup**
```html
<main id="main">
  <!-- zero-results -->
  <p class="rescount">No results for &ldquo;volcano&rdquo;.</p>
  <p class="note">Search matches headline wording — try a broader or differently-worded term.</p>
</main>
```
`.rescount` = mono, `--ink2`, tabular — same component as the populated `N results for "…"` count, so
the surface is consistent whether the search hit or missed. `.note` = sans 12 `--muted`.

**Behaviour** Server-rendered GET; the field keeps the submitted `q` as its value so the reader can edit
in place. `sanitize_query` already collapses blank → landing state.

**a11y** The count `<p>` is the first thing in `<main>`, so a screen reader meets the outcome
immediately on the post-submit page load — a full navigation, so **no live region needed**. Field has
`aria-label`; count is plain text (not colour-only).

---

## 4. Subscribe — success & error

Uses the **Notice bar** shared component (above). Both states land on the **index** so the reader keeps
their orientation (archive + form both in view); neither is a bare page.

### 4a. Success — `/?subscribed=1`
**Decision:** `--ok` notice with `✓` glyph + word, `role="status"`, placed at the top of `<main>` above
the (now hidden) form. After a successful subscribe, **hide the form** (they're done) and show the notice;
the archive stays fully visible below.

Copy: `Subscribed. The next issue will land in your inbox.`
```html
<p class="notice ok" role="status">
  <span class="ni" aria-hidden="true">✓</span>
  Subscribed. The next issue will land in your inbox.
</p>
```

### 4b. Error — `/?subscribe_error=1`
**Decision:** Change `subscribe`'s failure paths (503 not-configured, 500 Resend failure) to
`Redirect::to("/?subscribe_error=1")` — the current bare-text 500 is a dead end with no way back and no
retry. The redirect lands in the chrome index with a `--accent` ("bad") notice via `role="alert"` (a
failed action should be announced assertively) and **the form still present** so the reader retries
without losing their place. One honest message (we can't cheaply tell a hard failure from a duplicate).

Copy: `That didn't go through — something failed on our end. Please try again.`
```html
<p class="notice bad" role="alert">
  <span class="ni" aria-hidden="true">✕</span>
  That didn't go through — something failed on our end. Please try again.
</p>
<!-- subscribe form remains below, unchanged, for retry -->
```

**Behaviour / notes**
- Bad-email is caught client-side by `<input type="email" required>` before submit; the server assumes a
  valid address, so there's no separate "invalid email" server state to design.
- **Follow-up (not this pass):** if Resend returns a 422 duplicate, branch a distinct
  `/?already_subscribed=1` → `--ok` notice "You're already subscribed." Needs response-status parsing in
  `subscribe`; noted, deferred (keeps this pass to one honest error).

**a11y** Success = `role="status"` (polite); error = `role="alert"` (assertive — the reader must hear a
failed action). Both use glyph **+** word (redundant coding). Status colour is the semantic axis, not the
brand accent-for-decoration. Consider `tabindex="-1"` on the notice + focus-on-load so keyboard users are
placed at the outcome; optional, low priority.

---

## 5. Load-more — end-state & error-state (HTML-over-the-wire)

Load-more is a **degradable `<a>` + ~30 lines vanilla JS** over the `/archive?before=` fragment endpoint
(per `archive.rs` + the archive-endpoint spec). These states cover: **more available**, **loading**
(§6), **end reached**, and **fetch failed**.

### Region markup (baseline, more-available)
```html
<div class="loadmore" id="loadmore">
  <!-- degrades: without JS this <a> loads the next fragment; with JS it's intercepted -->
  <a class="btn secondary" id="loadMore" rel="next"
     href="/archive?before=2026-05-01">Load older issues</a>
  <!-- single polite live region: carries loading / end / error text to AT -->
  <p class="loadmore-status" role="status" aria-live="polite"></p>
</div>
```
`.btn.secondary` = transparent + `--line` border, `--r-input` (6px), sans 12/600 — the chrome's secondary
button. The status `<p>` is visually muted mono 12 and empty until something happens.

### Detecting the end — server sentinel (the 1 server change)
`archive_fragment` today returns only `<li>`s, so the JS **cannot** tell "30 rows and done" from "30 rows,
more to come." Decision: when `has_more`, the endpoint appends a **hidden sentinel** carrying the next
cursor; it's omitted at the end.
```html
<li class="more-sentinel" data-next-before="2026-04-01" hidden aria-hidden="true"></li>
```
Valid `<ul>` child, invisible, ignored by AT. The JS reads `data-next-before`, **removes the sentinel**,
appends the real rows, and updates the button's cursor. **Sentinel absent ⇒ end-state.** (~3 lines in
`archive_fragment`; deterministic, beats a "fewer-rows-than-limit" heuristic.)

### End-state (no more issues)
**Decision:** replace the button with one calm muted line; the oldest appended row gives the first-issue
date. Announce via the live region.

Copy: `That's the whole archive, back to {3 Feb 2026}.`
```html
<div class="loadmore" id="loadmore">
  <p class="loadmore-end" role="status">That's the whole archive, back to 3 Feb 2026.</p>
</div>
```
`.loadmore-end` = mono 12 `--muted`, centered under the list. (The `<a>` is removed from the DOM so there's
no dead/disabled button.)

### Error-state (fetch failed — network error or non-2xx)
**Decision:** never destroy already-loaded rows. Keep the button armed for retry (same cursor, unchanged),
and set the status line to the failure. Retry = click again.

Copy (status line): `Couldn't load older issues. Tap to retry.`
```html
<div class="loadmore is-error" id="loadmore">
  <a class="btn secondary" id="loadMore" href="/archive?before=2026-05-01">Load older issues</a>
  <p class="loadmore-status" role="status">Couldn't load older issues. Tap to retry.</p>
</div>
```
`.is-error .loadmore-status` = `--accent-ink` text (still readable; not colour-only — the word "Couldn't"
carries it). Button label stays "Load older issues" (clicking it *is* the retry).

**Behaviour (JS, per click)**
1. Prevent default; set `#loadMore` `aria-busy="true"`, `disabled`; append §6 skeleton rows; status →
   `Loading older issues…`.
2. `fetch(href)` → on 2xx: parse fragment; read + strip `.more-sentinel`; remove skeletons; append real
   `<li>`s; if sentinel present → set button `href` to the new `?before={cursor}`, clear busy/disabled,
   status → `` (empty); if sentinel absent → swap the whole region for the **end-state**.
3. on network error / non-2xx: remove skeletons; restore the button (clear busy/disabled); add `.is-error`;
   status → the error copy. Loaded rows untouched.

**No-JS degradation:** the `<a href="/archive?before=…">` still loads the next fragment (bare `<li>`s —
functional if unstyled). *Refinement (noted, not required):* a `href="/?before={cursor}"` server-rendering
the next full page in-chrome would degrade more gracefully; the fragment fallback is the accepted MVP.

**a11y**
- **One** `role="status"` (polite) region carries loading, end, **and** error text. Decision: polite (not
  `alert`) for all three — a background paging fetch failing is not an emergency; a polite announce after
  current speech is calmer and still heard. (If field testing shows errors get missed, bump only the error
  message to an `alert` region.)
- Skeletons are `aria-hidden` (§6) — the live region is the AT channel, not the placeholder rows.
- `aria-busy` on the button during load; button re-enabled on error so keyboard retry works; the removed
  end-state button leaves no focus trap (focus falls to the list / footer naturally).

---

## 6. Loading skeleton for load-more

**Decision:** a **calm alpha-pulse** placeholder per ui-design-craft §3 — a 1s two-step opacity/alpha pulse,
**no shimmer sweep**, **honours `prefers-reduced-motion`**. Three skeleton rows (calm, not a screenful),
each mirroring the issue-row grid so the layout doesn't jump when real rows replace them.

**Markup** (appended below the list on click; removed on settle)
```html
<li class="issue skeleton" aria-hidden="true">
  <span class="idx"><span class="bar bar-no"></span><span class="bar bar-date"></span></span>
  <span class="main"><span class="bar bar-line"></span><span class="bar bar-line short"></span></span>
  <span class="rt"><span class="bar bar-bias"></span></span>
</li>
<!-- ×3 -->
```

**CSS**
```css
.skeleton .bar{ display:block; background:var(--wash); border-radius:var(--r-input);
  height:.9em; animation:skel-pulse 1s ease-in-out infinite; }
.skeleton .bar-no{ width:2ch; }  .skeleton .bar-date{ width:5ch; margin-top:6px; }
.skeleton .bar-line{ height:1em; width:100%; }  .skeleton .bar-line.short{ width:62%; margin-top:8px; }
.skeleton .bar-bias{ width:64px; height:8px; }

@keyframes skel-pulse{ 0%,100%{opacity:.55} 50%{opacity:1} }   /* two-step, no sweep */

@media (prefers-reduced-motion: reduce){
  .skeleton .bar{ animation:none; opacity:.7; }                 /* static mid-alpha, still reads "loading" */
}
```
- `--wash` (the chrome's translucent-ink token) works over both themes; no per-theme override.
- Two keyframe stops (`.55 → 1 → .55`), ease-in-out, 1s — an alpha *pulse*, not a directional shimmer.
- Reduced-motion: hold a static `opacity:.7`; the placeholder shape + the live-region "Loading older
  issues…" (§5) still convey the state without animation.

**a11y** The skeleton is `aria-hidden="true"` — the meaning is carried by the §5 `role="status"` live
region ("Loading older issues…"), so AT users get a clean spoken state and never hear placeholder noise.

---

## Server-change checklist (implied by the above)

1. **404 chrome** — `render_not_found(state, variant)` + `.fallback(handlers::not_found)`; route
   `get_digest` (no-row → 404 NoIssue; malformed → Unknown), and redirect empty-DB `today`/`today_translate`
   → `/`. (§1)
2. **Empty archive** — index renders `.empty` line + hides list/segment/date-jump/load-more when
   `digests.is_empty()`. (§2)
3. **Search** — restyle no-query + zero-result copy to the chrome `rescount`/`note` components. (§3)
4. **Subscribe** — restyle success notice; **change failure paths to `Redirect::to("/?subscribe_error=1")`**;
   index reads `subscribed` / `subscribe_error` query flags → the correct Notice bar; keep the form on error.
   (§4)
5. **Load-more** — `archive_fragment` appends a hidden `.more-sentinel[data-next-before]` when `has_more`;
   the index ships the `.loadmore` region + the ~30-line JS (loading/skeleton/end/error). (§5, §6)

All copy above is final for this pass. All state colour uses the semantic axis (`--ok`/`--warn`/`--accent`)
with redundant glyph+word; the brand accent is never spent on status meaning.
```
