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

**Already real code (uncommitted in the working tree; branch `design/chrome-redesign-port-handover`
holds the committed docs/tooling at c210306):**
- `circulation/src/archive.rs` — `fetch_archive` (validated data layer) + `row_html` (renders one issue
  `<li>` with the bias `aria-label` split + HTML-escaping) + `archive_fragment` (`GET /archive?before=`
  HTML fragment) + 5 passing tests, clippy clean. **Verified against the real `data/digest.db`.**
- `/archive` route in `main.rs`.
- Committed: `design/tokens.css`, `bin/lighthouse`, `bin/a11y-check`, `make lighthouse`/`make a11y`.

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
- Sean has **uncommitted WIP** in `handlers.rs`/`main.rs`/`stats.rs`/`translate.rs`/newsroom (a
  translation/feedback stream) — keep your changes clear of it or coordinate before committing.

**Gates before claiming done:** `make ci` (fmt/clippy/test), `make lighthouse` (=100), `make a11y`,
`node cssdiff.js` (no drift), and for any surface: run it against `data/digest.db` and look at it.

Start with the foundation, then the index (off the already-proven endpoint). Don't gold-plate; ship
each surface verified before the next.
