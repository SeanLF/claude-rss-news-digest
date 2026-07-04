# Kickoff prompt — implement the news-digest redesign port

Paste this into a fresh session. The design is fully decided and durable; this is pure implementation.

---

You're implementing a fully-designed redesign of the `news-digest` project (Python `newsroom/` +
Rust/Axum `circulation/`). **All design decisions are made and written down — no design thinking is
left.** Read these in order before touching code:

1. `docs/2026-07-04-port-implementation-handover.md` — THE entry point: every surface + interaction
   mapped to an existing route/query or "needs building", the build order (§7), verification gates.
2. `docs/design-system.md` — the visual spec (cross-page chrome contract, tokens, components, 3-state
   theme toggle, token/CSS/font delivery). Authoritative sections are listed in the handover; the early
   "Circulation chrome" migration sketch is superseded.
3. `docs/superpowers/specs/2026-07-04-archive-endpoint-index-realdata-design.md` — the archive/index
   slice. **The later "Research findings" + "Interaction model" sections supersede the earlier JSON
   sections** (architecture was corrected to HTML-over-the-wire mid-spec).
4. `docs/2026-07-04-states-error-empty-loading-design.md` — 404 / empty archive+search / subscribe
   success+error / load-more end+error / loading skeleton.
5. `docs/2026-07-04-stats-design-final.md` — final stats metric set + geographic lens.
6. Visual reference: `scratch/chrome-mockups/*.html` (gitignored; regen via `build.py`). `cssdiff.js`
   is the cross-page consistency gate. The mockups are placeholder-data; `design-system.md` is durable.

**Already real code — now all COMMITTED** on branch `design/chrome-redesign-port-handover` (tip
`1a1a7a2`, base `main` = `d04f9ad`). The earlier "uncommitted in the working tree" note is stale.
- `circulation/src/archive.rs` — `fetch_archive` (validated data layer) + `row_html` (renders one issue
  `<li>` with the bias `aria-label` split + HTML-escaping) + `archive_fragment` (`GET /archive?before=`
  HTML fragment) + 5 passing tests, clippy clean. **Verified against the real `data/digest.db`.**
- `/archive` route in `main.rs`; `design/tokens.css`, `bin/lighthouse`, `bin/a11y-check`,
  `make lighthouse`/`make a11y`.
- These landed inside `7617756`, a **WIP checkpoint that bundles two half-integrated streams welded in
  `main.rs`/`handlers.rs`**: the chrome/archive port AND a translation/feedback stream. If you want clean
  per-stream history before landing, re-split it (rebase — it's no longer HEAD); otherwise just build
  forward. Tree is clean, all CI-green.

**Build order:**
1. **Foundation:** `include_str!` `design/tokens.css` into circulation (collapse the 6 duplicated
   `:root` blocks; inline per-page); the `/assets` **hashed-font route** (`include_bytes!` woff2, content
   hash, immutable); newsroom `{{STYLES}}` reads tokens.css; the **digest-blob `@font-face` injection**
   (circulation injects at the `</head>` seam — `handlers.rs:586`, where `DIGEST_NAV_CSS` goes; newsroom
   only uses the family name); a parity canary test.
2. **Index (the proven slice):** rewrite `render_index` (`templates/index.rs`) to `chrome_v12` + render
   real rows via `row_html` + the load-more per the settled interaction model + the error/empty/skeleton
   states. Wire `handlers::index` to `fetch_archive` and the `?before=`/`?year=` params. Verify: `cargo
   test`, run against `data/digest.db`, screenshot, `make lighthouse` + `make a11y`, `node cssdiff.js`.
3. **Other chrome pages** (sources / stats / threads / thread-detail / feedback / search) to the mockups.
4. **Digest body port** (newsroom dispatch design → template + `digest.css`) + circulation `digest.rs`
   chrome injection. **Ask Sean first** — it's the biggest piece and email-coupled.
5. **Deferred (need instrumentation):** stats new metrics + "Planned" tiles; thread per-fact source
   resolution (going-forward, at installment-write time).

**Constraints (non-negotiable, from the research):**
- No JS framework, no build step — **vanilla JS only** (~30-line load-more).
- **HTML-over-the-wire, not JSON** for load-more (the endpoint returns `<li>` fragments from the same
  `row_html`, over a degradable `<a>`; that `<a>` is the no-JS URL fallback).
- **Per-page inlined CSS** composed from `tokens.css` (DRY source, inlined output — best Lighthouse).
- **TDD** — temp-dir sqlite fixtures (copy the pattern in `archive.rs` tests / `handlers.rs:717`).
- **Status colour uses the semantic axis** (`--ok`/`--warn`/accent-as-danger), never the brand accent;
  one alarm axis per screen; every status is icon+colour+text (never colour-only).
- Load-more a11y: focus-to-first-new-row, `aria-live "X of N"`, real `<a>`/`<button>`, no auto-scroll.

**Two reconciliations (implementer picks, not decisions):**
- End-detection: the states doc proposes a hidden `.more-sentinel[data-next-before]`; the segment doc
  uses `data-date` on `<li>` + row-count. Pick one.
- The translation/feedback stream (`handlers.rs`/`main.rs`/`stats.rs`/`translate.rs`/newsroom) is now
  **committed** (in the `7617756` checkpoint), not uncommitted — build forward without clobbering it;
  it shares `main.rs`/`handlers.rs` with the chrome-port you'll be rewriting.

**Gates before claiming done:** `make ci` (fmt/clippy/test), `make lighthouse` (=100), `make a11y`,
`node cssdiff.js` (no drift), and for any surface: run it against `data/digest.db` and look at it.

Start with the foundation, then the index (off the already-proven endpoint). Don't gold-plate; ship
each surface verified before the next.

---

**Pre-rebrand hardening (read `docs/2026-07-04-pre-rebrand-hardening.md`).** A scan before this port
already shipped the standalone fixes (db.py FK pragmas; cluster/merge/util robustness) and the safe
pre-rebrand items (pip-audit in ci-full; inline_styles logging; `fetch_stats_data` tests). The items
below were **deliberately left for this port** because they're coupled to the chrome/template rewrite
— do each as you touch its surface:

- **Route all brand strings through `DIGEST_NAME` FIRST** — scattered in `render.py`/`broadcast.py`,
  several bypass it (`render.py:389`, `broadcast.py:116/159/168/191/201/230…`, medal emojis
  `render.py:371`). Doing this first makes the rename one edit, not grep-and-pray.
- **Placeholder safety in `render.py`** — `{{MUST_KNOW}}`/`{{SHOULD_KNOW}}` fill (`329`) has no
  existence check (mirror `replace_placeholders:403`), and several strip regexes (`437-486`) hard-match
  exact template copy. After replacement, scan for residual `{{…}}` and raise. A renamed placeholder
  otherwise ships a zero-article digest silently — exactly what a template rewrite triggers.
- **Index rewrite:** extract `fetch_index_page` (or reuse `archive::fetch_archive`) — `handlers.rs:56`
  mixes query + inline HTML, duplicates the digest-list query (3 forms), and loads ALL digests unbounded
  per request. Paginate the first render (the `/archive` load-more endpoint already exists).
- **`get_digest` (`handlers.rs:586`):** replace the 5 sequential `inject()` string splices with
  structured composition rather than porting the splice chain forward.
- **`Tier` enum** for `"must_know"/"should_know"` before the archive/stats/search structs are
  re-consumed by new templates; extract **`open_ro(db_path)`** (READ_ONLY connect + busy_timeout
  boilerplate is copy-pasted ~8 sites).

**Modern techniques worth adopting — but ONLY where they map to a surface this project actually has
(each below was verified against the code). Buildless/vanilla, consistent with the constraints;
implementation choices for the settled design, not design changes; the PE ones are optional. Detail in
`docs/standards/`:**
- **The load-more / HTML-over-the-wire fragment swap (exists — the ~30-line load-more):** wrap the
  `<li>` insertion in same-document **View Transitions** (`document.startViewTransition`, Baseline) for
  a smooth swap. Optional PE, no build.
- **The 3-state theme toggle (exists):** `light-dark()` + `color-scheme` can replace dual light/dark
  selectors. An option, not a mandate.
- **Story headlines (exist):** `text-wrap: balance`/`pretty` — cheap editorial polish for a news list.
- **Circulation (Rust):** add **`tracing`** (the server has no structured logging today) and unify
  handlers behind one **`AppError: IntoResponse`** (the scan found inconsistent error types) — both
  thin every rewritten handler.
- **Digest email port:** keep table layout (classic Word-engine Outlook lives to ~2029); verify the
  one-click-unsubscribe (RFC 8058) headers on a real send. See `email-rendering.md`.

*Explicitly NOT suggested (verified inapplicable): customizable `<select>` and CSS anchor positioning —
there is no `<select>` picker and no popovers/menus/tooltips in the app. The translate feature is a
header/`?lang=` redirect to a Google-Translate proxy, not a dropdown. `@scope` / Speculation Rules were
dropped as speculative against the settled chrome/nav approach.*

**Still-open STANDALONE item (not coupled; NOT yet fixed — needs its own focused pass):**
`run.py --resume` (and `--write-only`) route through `_render_record_deliver`, which skips
`archive_selections`/`archive_clusters`/`archive_run_artifacts` + `_process_story_threads` that the
full path runs (`run.py:502-507`). A resumed digest delivers to subscribers but misses the archive
artifact tables and thread-continuity state. Fixing it means converging the paths **idempotently**
(resume can re-run) with a **side-effect test**, and deciding whether `--write-only` should archive
too. See the hardening doc.
