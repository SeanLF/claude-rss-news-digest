# Digest body port — design (validated)

**Date:** 2026-07-04 · **Branch:** `design/chrome-redesign-port-handover` · **Status:** design approved, pre-implementation

Last piece of the chrome/redesign port: bring the **email digest body** onto the dispatch mockup design.
LIVE EMAIL to real subscribers (11) — high stakes. This doc is the validated design; a separate
implementation plan follows.

## Goal

Port `scratch/chrome-mockups/digest_v1.html`'s body markup + CSS onto the newsroom render path, so the
daily email (and its web archive view) match the redesigned chrome surfaces.

## Key insight (verified): one operation, not two

The mockup's CSS `:root` is **byte-identical** to `design/tokens.css`'s digest family
(`--bg --ink --ink2 --muted --hair --accent --accent-ink --bias-l/c/r --serif --sans --mono`, same
light/dark hexes). So porting the body to the dispatch design **simultaneously performs the deferred
token rewire** — the old `digest.css` tokens (`--text`, `--border`, `--accent:#c45a3b`, `--notice-bg`)
are fully replaced by the shared digest family, and it activates the Source Serif 4 `@font-face` that
circulation already injects into the web view (currently inert because the stored blob is still Georgia).

## Decisions (this pass)

- **Deck line: dropped** for now. The mockup's `.deck` at-a-glance line is omitted; masthead flows
  straight into the sections. (Revisit later if wanted.)
- **Masthead: match the mockup fully** — brand, edition `No. N`, filed time, dateline with story/read
  counts, AI-written notice tag. Every datum has a source (below).
- **Email hardening: residual-`{{...}}` placeholder sweep only.** RFC 8058 `List-Unsubscribe` /
  `List-Unsubscribe-Post` are **already set by Resend at send time, DKIM-signed** (verified on the
  2026-07-04 production send — both headers present, real per-recipient token, inside the DKIM `h=`
  list). Adding them in `broadcast.py` would duplicate/conflict with Resend's signed token — so we do
  NOT touch headers. The genuine gap is that leftover `{{...}}` placeholders ship silently; add a sweep.

## Data sources (all verified present)

- **Edition No. N** — derived digest count, the same rank `circulation/src/archive.rs` already uses
  (`SELECT COUNT(*) FROM digests WHERE date <= d.date`). Newsroom has DB access; compute at render time.
- **Filed time** — existing `{{GENERATED_AT}}` / generation timestamp.
- **Counts** — existing `{{STORY_COUNT}}` and `{{READING_TIME}}`.
- **AI-written notice** — existing `.ai-notice` content, restyled as the mockup's `.notice`/`.tag`.

## Files to change (7)

Line numbers verified against current `main..HEAD`.

1. **`newsroom/templates/digest.css`** — rewrite to the mockup's component CSS. **No `:root`** (tokens
   come from `tokens.css`). Components: `.paper`, `.masthead`/`.brand`/`.issue`/`.dateline`/`.tick`,
   `.notice`/`.tag`, `.section`/`.num`/`.name`/`.rule`, `article`/`.head`/`.eyebrow`/`.loc`/`.lede`/
   `.why`/`.varies`, `.brief`, `.anchor`, sources (`.srcbox details`/`summary`/`.biasbar`/`.seg.l/.c/.r`/
   `.spread-label`/`.src-table`), `.spread.email-only`, footer. **Default state = EMAIL**
   (`.web-only{display:none}`) — the web view flips it (see #6). Responsive `min-width:760px` (800px
   paper, 680px reading col) / `max-width:400px`.
2. **`newsroom/templates/digest-template.html`** — rewrite shell: preheader, masthead (brand
   "Sean's Daily <b>Digest</b>", `.issue No.N / Filed`, `.dateline` tick / time / read / counts),
   `.notice` (AI-written tag), **no deck**, section 01 Must Know (`id=s-mk`) + section 02 Should Know
   (`id=s-sk`), footer. Proper `h1→h2→h3` tree, `<section aria-labelledby>`, `<time>`. NO web chrome
   topbar / theme JS here (circulation injects those for web; email has none). Keep placeholders
   `{{MUST_KNOW}}` `{{SHOULD_KNOW}}` `{{STYLES}}` `{{DATE}}` and the triple-brace
   `{{{RESEND_UNSUBSCRIBE_URL}}}` (Resend fills per-recipient). New: `{{ISSUE_NO}}`, `{{FILED_TIME}}`;
   counts reuse `{{STORY_COUNT}}`/`{{READING_TIME}}`.
3. **`newsroom/src/render.py`** — rewrite `render_article` (def at **line 210**) to Story/Brief markup:
   - Story: `.head` + optional `.eyebrow` (`Ongoing · day N`, below the headline) + `.lede` +
     `.why` (border-left accent) + `.varies` + sources block.
   - Brief: `.brief h3` + optional eyebrow + `.summary` + sources block.
   - Bias bar: group sources by bias bucket (l/c/r), emit
     `<span class="biasbar"><span class="seg l" style="flex:{n}">…` + spread-label
     `"{N} sources · {a} left · {b} center · {c} right"` (singularize "1 source"; drop empty buckets).
   - Sources block: web-only `<details>` table (rows ordered left→center→right, numbered article links
     per outlet) + email-only static `.spread` glyph + "view all N sources online" link.
   - **Keep** `html.escape`, `is_safe_url`, `_has_article_path` guards.
   - `{{STYLES}}` = `tokens.css` + `digest.css` (currently only `digest.css`).
   - Compute edition `No.` (digest count) + filed time.
   - **Add residual-`{{...}}` sweep** after fill (raise on any leftover `{{…}}`). `replace_placeholders`
     is at **line 376** (already guards `{{DIGEST_NAME}}/{{DATE}}/{{STYLES}}` at 404-407, but not
     `{{MUST_KNOW}}/{{SHOULD_KNOW}}` and does no residual scan).
4. **`newsroom/config.py`** — add `TOKENS_FILE` (path to `design/tokens.css`).
5. **`newsroom/Dockerfile`** — add `COPY design/ ./design/` so `tokens.css` is available at runtime
   (currently copies src/templates/sources.json/migrations/bin only — verified no `design/`).
6. **`circulation/src/handlers.rs` `get_digest` (def at line 628)** + **`templates/digest.rs`** — the
   web view already injects the font-face, the top-bar nav (`digest_nav_html`), the `.email-only`→web
   flip (`DIGEST_NAV_CSS`), and its own footer (`web_footer_html`). Reconcile these with the new class
   names/structure. The digest keeps its OWN doc footer (per prior decision) and uses its own `--hair`
   tokens for the top bar, not chrome's `--line`.
7. **`newsroom/src/broadcast.py`** — add a **`--test-send <addr>`** path: render + email-prepare the
   digest and send via Resend `Emails.send` to a single arbitrary address (production uses the
   Broadcasts API + audience, which can't target an arbitrary address). Reusable QA tool. **No header
   changes** (Resend owns List-Unsubscribe).

## Email dual-render (the hard part)

- `resolve_css_variables` (line 134) inlines `var(--x)`→hex for email and strips the
  `prefers-color-scheme` dark block. **Keep.**
- Visibility model (corrected): `prepare_for_email` does **not** flip `.email-only`/`.web-only`. The CSS
  **default is the email state** (`.web-only{display:none}` baked into `digest.css`); circulation's
  `DIGEST_NAV_CSS` injects the reverse for web. So author the new CSS with the email state as default.
- **Outlook (Word engine, no flex)**: the flex `.masthead` AND the flex `.biasbar` need table / %-width
  fallbacks in the email render. Build table equivalents (email-only) alongside the flex versions
  (web-only). This is the real email engineering and the top risk.
- Georgia fallback preserved (email never receives the woff2; `--serif` resolves to
  `"Source Serif 4", Georgia, …`, and email inlines to the Georgia-first stack).

## Verification / acceptance gate

- **Web view (can verify locally):** `cd newsroom && python src/run.py --write-only` renders
  `selections.json` → HTML. Use fixture `newsroom/tests/fixtures/prod_baseline_selections.json`
  (5 must_know / 8 should_know). Screenshot + `bin/a11y-check` + Lighthouse against a served copy;
  check light **and** dark.
- **Email (verify via real sends):** `--test-send` to `sean.floyd@hotmail.com` (Outlook / Word engine —
  the hard case) and `sflow2008@gmail.com` (Gmail). Confirm: table masthead + biasbar render, email-only
  shown / web-only hidden, vars inlined to hex, Georgia fallback, unsubscribe link works, and the
  `multipart/alternative` text part isn't junk.
- **Gate:** `make ci` (pytest + rust) green; residual-placeholder sweep passes.
- **Safety:** the port lives on this branch; template/CSS only reach subscribers on **deploy**. Do NOT
  deploy until the test sends pass. No auto-send risk in the meantime.

## Out of scope (separate passes)

- Thread per-fact source links (newsroom, at installment-write time; going-forward only).
- `run.py --resume` archive-artifacts convergence (standalone).
- `DIGEST_NAME` brand routing — already an env default (`render.py:391`, `broadcast.py:116,159`); only
  stray medal-emoji strings would remain, not part of this port.

## Risks

1. **Outlook render** — flex→table fallbacks are fiddly and only verifiable by a real send. Mitigation:
   the hotmail test send is a hard gate before deploy.
2. **Token rewire regressions** — replacing every `digest.css` token at once. Mitigation: the mockup is
   already value-identical to `tokens.css`, and the web view is fully verifiable locally (light + dark).
3. **Silent placeholder breakage** — a renamed/missing placeholder shipping blank. Mitigation: the new
   residual-`{{...}}` sweep raises on any leftover.
