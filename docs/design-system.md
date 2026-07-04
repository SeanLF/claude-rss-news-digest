# Digest Design System

Source of truth for the digest's visual language (web + email surfaces). Derived from
the agreed "dispatch" direction and validated in an interactive mockup (Lighthouse a11y
100). The mockup drives this doc; this doc drives the templates — the exact rendering may
evolve, but tokens, scale, and component patterns below are canonical.

Reference mockup: `$CLAUDE_JOB_DIR/tmp/dispatch_artifact.html` (published Artifact).

## Lineage with seanfloyd.dev

Derived from — but not a clone of — the author's site (`~/Developer/seanfloyd.dev/public/css/base.css`).
They're related properties with different jobs (a personal site vs. a daily news briefing), so the
digest **inherits a shared foundation and is free to diverge everywhere else where it reads better**.

**Shared foundation (canonical — keep byte-identical to base.css; it is upstream):** the shared colour hexes
in both themes (`--ink, --ink2 [site `--ink-2`], --muted, --hair, --accent, --accent-ink, --ok`),
the Source Serif 4 face (`400 600` woff2) + `--serif`/`--mono` stacks, `--label` = 11px, and the focus
treatment (`2px solid --accent`, offset 3px). If base.css re-tunes an AA-critical value, propagate it here.
(`--bg` used to be shared but is now digest-owned — cooled to `#fafaf8`, see the token table.)

**Digest-owned (deliberate divergence, don't force back onto the site):** the **serif-display lead**
(the site is sans-display), the integer 8-step type scale, the 3-tier caps `{.08/.12/.18}`, the 4px
spacing grid, `--warn`, and the *strict* `--accent`(marks)/`--accent-ink`(text) split (the site is looser).
These are additive discipline, in the same spirit but re-systematised for a serif wire briefing — not
extracted from the site's actual ladders (only `--label:11` truly carries over).

**Recorded drift:** `--sans` is a deliberate email-hardening fork (adds Segoe UI/Arial for cross-client
coverage). `--faint` was *merged into* `--muted`: the site keeps them as two AA-tuned values
(`#747168` vs `#6f6d65`); the digest collapses to the darker `--muted`, which is safe (anything passing
on `--faint` passes on `--muted`) — do **not** reintroduce `--faint` at its lighter hex (it would revert a
documented 3.32:1 fail). `--ink-2`→`--ink2` is a cosmetic rename. No single shared token source across
repos (different stacks; email needs literal-hex inlining anyway) — the contract is this documented one-way lineage.

## Principle

An editorial *wire briefing*: Source Serif 4 display, monospace tracked datelines and
labels, hairline rules, one restrained coloured word-splash. Boldness spent in one place
(the accent word + functional accent on labels/links); everything else quiet.

## Color tokens

Define as CSS custom properties; style components through tokens only (no raw hex in
component rules). Redefine under `@media (prefers-color-scheme: dark)`, `:root[data-theme="dark"]`,
`:root[data-theme="light"]`. For email, `render.py:resolve_css_variables` inlines these to literal hex.

| Token | Light | Dark | Role |
|-------|-------|------|------|
| `--bg` | `#fafaf8` | `#16150f` | page ground — **cooled from the site's `#faf9f6`** to a soft neutral paper (deliberate digest divergence: the briefing reads less cream/orange than the site) |
| `--ink` | `#191917` | `#eceae1` | headlines, lede, emphasis, top masthead rule |
| `--ink2` | `#3b3a36` | `#cbc9bd` | body copy |
| `--muted` | `#6f6d65` | `#9d9a8e` | meta, eyebrows, sources, dateline, bias codes |
| `--hair` | `#dddcd4` | `#2d2b22` | rules, borders, separators |
| `--accent` | `#b1352a` | `#e2675b` | **non-text marks only**: why-border, dateline tick, focus outline, translate pill border/bg |
| `--accent-ink` | `#8f2a20` | `#ec7d72` | **accent text/links** (contrast-safe): the `Digest` word-splash, mono labels, section number, source links |
| `--bias-l/c/r` | `#5f7391`/`#928f86`/`#b0604e` | `#8194b3`/`#a5a196`/`#cf7a68` | source **bias-spread bar** segments (low-sat slate/grey/terracotta, non-partisan). Define in **all four** token blocks incl. `data-theme`; email needs `resolve_css_variables` inlining |

The `--accent` / `--accent-ink` split is semantic and load-bearing — keep it. Do not use
`--accent` for text (fails AA at small sizes) or `--accent-ink` for structural marks.
(Retired: `--faint` merged into `--muted`; `--raise`, `--hair-2` unused — do not reintroduce without a use.)

## Typography

- **Display + body**: Source Serif 4 (self-hosted woff2; `~/Developer/seanfloyd.dev/public/fonts/`), fallback `Georgia, "Times New Roman", serif`. Body 400, display 600.
- **Utility / section names**: system sans (`-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`).
- **Datelines / labels / meta / bias**: `ui-monospace, "SF Mono", Menlo, Monaco, monospace`.

### Type scale (8 steps, px)

| Step | Use |
|------|-----|
| 10 | micro-caps labels (block labels, bias code, AI-WRITTEN tag, issue) |
| 11 | secondary caps / meta (eyebrow, dateline, section name) |
| 12 | small sans UI (sources, footer, translate, backlink, notice, section number) |
| 14 | secondary body (reporting-varies, brief summary) |
| 16 | secondary UI / chrome text (why-it-matters now uses body size — see Story) |
| 18 | body, lede, brief headline |
| 25 | story headline |
| 27 | brand wordmark |

**Responsive tiers** (same ratios, scaled for reading comfort — mirrors the original digest's 18→20px desktop bump):
- **mobile** (`≤400px`): body 17, story head 22, brand 23; measure full-width.
- **base** (tablet): the 8 steps above; measure ~660px.
- **desktop** (`≥760px`): body 20, lede 20, story head 29, should-know head 22, brand 30; all reading prose (summary, why-it-matters, reporting-varies, should-know) at body size; **the frame widens to 800px but the prose column (`article`) caps at ~680px** — see measure note below.

### Reading measure (evidence-grounded)

Line length is the single highest-impact readability lever, and the one parameter worth pinning to evidence (45–75 characters/line, ~66 ideal; Baymard field-tests tie over-wide measure directly to *reading abandonment*; ≤80 CPL is the WCAG AAA ceiling). At ~0.5em average char width for Source Serif 4, CPL ≈ measure ÷ (0.5 × body-px):
- base 660px / 18px ≈ **73 CPL** (edge of the band — fine).
- desktop **frame 800px** but **prose column capped 680px** / 20px ≈ **68 CPL** (in the sweet spot). Full-width masthead / section rules / dateline over a narrower reading column is a deliberate editorial device, not just a constraint.

The rest was checked against the reading-science literature and **validated, keep as-is**: 18→20px body (long-form sweet spot); line-height **1.6** (correct for this measure — wide measures want 1.6–1.7, and it partly offsets return-sweep cost); contrast `#191917`/`#fafaf8` ≈ **16.6:1** (a deliberate step down from pure-black's 21:1 to cut fatigue, still 2×+ AAA); serif body (a wash vs sans on hi-dpi — a brand choice, well-supported); and the **summary-first → labelled-callout → tiers** structure (near-textbook BLUF + layer-cake scanning — the design's biggest strength; NN/g measured 33%→65% key-fact recall from this pattern). Deliberate override: the review suggested dropping should-know prose to 16/17px for tier contrast, but we keep **all reading prose at body size** — Sean's call, readability over size-hierarchy; the tier signal is headline size + depth, never shrunk prose.

No half-pixel sizes — within a tier, the scale is the scale.

### Letter-spacing (3 tiers for tracked caps)

- **`.08em`** tight labels: eyebrows, section number, bias codes, issue block
- **`.12em`** standard: dateline, block labels (WHY IT MATTERS / HOW REPORTING VARIES), AI-WRITTEN tag
- **`.18em`** wide: section name only
- Serif display headings: `-.01em`

### Weights

Serif display 600 / body 400. Mono block-labels **600** + `--accent-ink` (WHY IT MATTERS and
HOW REPORTING VARIES share one identical treatment). Sans section-name 700. All inline links 400.

## Spacing

**4px grid** — every margin/padding/gap a multiple of 4 (common steps 4 / 8 / 12 / 16 / 24 / 32 / 40 / 48 / 56 / 96). No non-multiples; no inline spacing overrides (use a class). Touch-target padding is exempt where it must meet the ≥24px hit-area rule below.

## Components

- **Top utility row** — `← Past digests` (sans 12 muted, left) + `文A Translate` terracotta pill (accent-ink, right). Web-only; email header carries Subscribe / View online / Translate instead.
- **Masthead** — brand (serif 27/600, the word `Digest` in `--accent-ink` = the one coloured splash) + issue block (mono 10, No. + filed), `2px solid --ink` bottom rule.
- **Dateline** — mono 11 `.12em` caps, leading `■` tick in `--accent` (`aria-hidden`), slash-separated **real data only** (`<time>` date / read-time / must-should counts); the `/` separators are `aria-hidden`.
- **AI notice** — framed by `--ink` top + `--hair` bottom hairlines; mono `--accent-ink` **AI-WRITTEN** tag + sans 12 `--ink2` text. Prominent, not a filled alert box.
- **Section rule** — the header of a `<section aria-labelledby>`: mono number (`--accent-ink` 12 `.08em`, `aria-hidden`) + the tier's `<h2>` name (sans 700 11 `.18em`) + flex `--ink` hairline (`aria-hidden`).
- **Story (must-know)** — an `<article>`, **headline-led** (headline is each story's top scan-target): `<h3>` headline (serif 25/600, desktop 29, `text-wrap: pretty` — **not `balance`**, which breaks hyphenated compounds at their hyphen and leaves a dangling "largest-"; hover/focus `#` anchor) → status line (eyebrow: `ONGOING · day N`, mono 11 `.08em`, status word in `--accent-ink`, **below** the headline like a dateline; **ongoing threads only** — non-thread stories have no eyebrow) → lede (serif, **body size** `--ink`) → why-it-matters (`--accent` left border, mono accent label, **body size** — the editorial payoff, set apart by the border+label, not by shrinking) → reporting-varies *(conditional, data-driven; **body size** — readable, not a shrunk footnote)* → sources. Consecutive stories separated by a `--hair` top rule.
- **Brief (should-know)** — an `<article>`: `<h3>` headline (serif 20/600, desktop 22 — the tier signal is the *headline* size, not shrunk prose) → eyebrow (ongoing only) → summary (**body size** `--ink2`) → sources. Compact tier: distinguished by the smaller headline and by having no why/varies, **not** by shrinking the reading text. Both tiers show sources.
- **Deck / standfirst** — the digest's one-line preheader (already generated for email preview + index subtext) surfaced **below the AI notice** as a small muted "at a glance" line: serif 16/18px `--muted`. Deliberately de-emphasized — it recaps the headlines you're about to read, so its real value is email-preview/index, not competing with the stories.
- **Sources** — three evidence-grounded tiers (Ground News / Techmeme / Perplexity all converge here; NN/g: never collapse a comparison/scan task; trust research: a *stack* of visible cues beats one buried one). For an *AI* digest, source verifiability **is** the credibility mechanism.
  The whole sources block sits at the **story foot** (after reporting-varies), so the reading flow (headline → status → summary → why → varies) is uninterrupted.
  1. **The bias glyph (always visible)** — a thin low-sat spectrum bar (`--bias-l` slate / `--bias-c` grey / `--bias-r` terracotta, muted, non-partisan) sized by the L/C/R outlet split + a mono label `11 sources · 5 left · 4 center · 2 right`; `role="img"` with the spread as `aria-label`. This is the "all sides at a glance" trust signal — breadth is a *comparison* task the research says never to collapse, so it's always shown.
  2. **The glyph IS the `<summary>`** — clicking the spread expands a small `<table>`: Outlet · Leaning · Articles (numbered links), **spectrum-ordered** (far-left→far-right). Outlet name is plain text (the numbered links are the links); bias **spelled out** (never `L·L`/`CTR`). The full per-article breadth (`NYT World 1 2 … 8`) lives here — full verifiability, one click, zero inline clutter.
  3. **Email dual-render.** The `<details>` (`.srcbox`) is `web-only`; email shows a static `.spread` glyph + a "view all N sources online" link. Rationale: `<details>` renders *expanded* in Gmail/Outlook (degrades visible, never hidden), which would dump the whole table into every inbox story — so email keeps the glyph (trust cue) + links out; the web view is interactive. (History: fully dropping article links was **reverted** — kills verifiability; a name-links-inline-only version and inline per-source `<details>` were both abandoned; the glyph-summary + collapsed table is the convergence of Ground News / NN/g / trust-research.)
- **Share anchors** — a per-story permalink `#`, drawn with a **CSS `::before` (not DOM text)** so it never leaks into Safari Reader or a screen reader; the `<a>` sits at the **end** of the heading with a **headline-bearing** `aria-label` (`Copy link: {headline}`) — unique per story (`opacity:0` does not hide it from AT, so identical labels would be non-distinguishable). `opacity 0 → 1` on `:hover`/`:focus-visible`; the anchor box is widened to touch the headline so there's no hover dead-zone reaching it. Shown only `≥760px` (pointer affordance; hangs in the margin, would clip the narrow measure). Web-only. NB: `opacity:0` does **not** hide from AT — that's why the glyph must be CSS-generated, not text.
- **Footer** — three sans lines: nav links / actions (translate · feedback) / attribution. No mono-crammed strip.

## Machine-facing metadata (not for readers)

Structured/semantic-web markup is invisible to readers — it's for machines that route readers *to* the digest: search engines, social/chat unfurls, RSS/news aggregators, AI crawlers. Priorities:
- **Open Graph + Twitter Card** — the high-value piece (link-share unfurls). **Already present** on circulation (`og:title/description/type/url/site_name/image`, `twitter:card=summary_large_image`, 1200×630 image). Keep.
- **Schema.org JSON-LD** — *not* present, optional. `NewsArticle` per story (if per-story pages exist) or a `CollectionPage`/`Blog` for the digest index would help search rich-results and aggregator/AI comprehension. Reader-facing value is zero; distribution value is modest — worth it only if search/aggregator discoverability matters (skip if readers arrive by email/direct). Low priority.

## Email constraints

- Source Serif 4 loads in Apple Mail / iOS; Gmail & Outlook fall back to Georgia — the structure (labels, rules, dateline) carries the identity, the font is a bonus.
- Masthead, dateline, and the **bias-distribution bar** must degrade for **Outlook desktop** (Word engine, no flexbox) — build them table/`width:%`-based for email, not `flex`. The bias bar's mono text label (`5 left · 4 center · 2 right`) is the universal fallback if the bar itself can't render.
- **`<details>` degrades to *visible*, never hidden** (verified: Apple Mail supports it; Gmail swaps the tags for `<u></u>`, Outlook/Yahoo strip them — content shows expanded). Safe, but "expanded" means verbose — so the Tier-3 source accordion is `web-only` (`display:none` in email) to keep the inbox clean; the full breakdown is a "view online" affordance.
- Hover/focus share anchors are web-only (email strips them).

## Circulation chrome (index, sources, stats, search, threads, feedback)

The circulation web pages predate this system and use an **older, divergent vocabulary**
(`--ruby`/`--border`/`--ink-light`/`--green`/`--yellow`), the **wrong fonts** (Georgia +
system sans, no Source Serif 4, no mono), an off-scale rem-fraction size ladder, and ad-hoc
spacing. Each page is a standalone document with its own duplicated `:root`. Bringing them
under this system:

**Consolidate tokens.** Collapse the six duplicated `:root` blocks into one shared const with
the canonical names/values. Migration map:

| Old (circulation) | New token | Notes |
|---|---|---|
| `--bg #fafaf8` | `--bg #faf9f6` | value drift |
| `--text #1c1c1a` | `--ink #191917` | rename |
| `--text-muted #6b6b67` | `--muted #6f6d65` | rename |
| `--ink-light #4a4a46` | `--ink2 #3b3a36` | rename (body) or `--muted` (meta) |
| `--border #e0e0da` | `--hair #dddcd4` | rename |
| `--ruby #c45a3b` | `--accent #b1352a` (marks) / `--accent-ink #8f2a20` (text) | **one token → two**; links/fills that are text use `--accent-ink` |
| `--ruby-hover #d4897a` | *(retire)* | fails AA; hover = underline reveal, colour stays `--accent-ink` |

Update the `SKIP_LINK_CSS` / `SECTION_ANCHOR_CSS` fallbacks in `digest.rs` (currently
`var(--ruby,#c45a3b)` / `var(--text-muted)`) when renaming, or they silently break. Replace
raw `rgba(196,90,59,…)` selection/row-hover and the hardcoded `#c45a3b` favicon/OG fill with
`color-mix(in srgb, var(--accent) …)` / `#b1352a`.

**Semantic status axis (separate from the brand accent).** Status colour is functional, not
decorative, so it gets its own tokens, NOT the accent hue. Adopt seanfloyd.dev's `--ok`
(`#2f9b5e` light / `#46c184` dark) and add `--warn` (an AA-safe amber ~`#8a6a00` / `#d9b34d`);
use `--accent` for the danger/bad state. Apply to source-health good/warn/bad, thread status
badges, factuality badges, subscribe success. (Also fixes an existing bug: `--green` is
`#2d7a3a` on most pages but `#5a9e4b` on sources.rs — one name, two colours.) Keep meaning
non-colour-only (badge text + label), per the a11y rule below.

**Border-radius.** One `--radius: 3px`, used **only on form controls** (inputs, buttons) for
affordance. Badges, status pills, period toggles, month headings → flat/hairline (0 radius) to
match the wire aesthetic. The translate control keeps its `999px` pill (the single exception).

**Fonts for chrome.** Load the same self-hosted Source Serif 4. Roles: page `h1`/titles →
serif **600** (not sans 700); running text → serif; section labels, badges, table headers,
meta, dateline-style text → **mono tracked caps**; nav/buttons/form UI → system sans.

**Component targets** (re-quantise every value to the 8-step type scale + 4px spacing):
- **Page `h1`** → serif 27/600, `-.01em` (was sans 40.5px/700/−.025em).
- **Section labels** (`h2`, `.month-heading`, `.column-header`) → mono 11 `.08em` uppercase `--muted`, `--hair` underline.
- **Nav** (`.meta-links`, `.page-nav`, `.back-link`) → unify to one treatment: sans 12 `--muted`, hover `--accent-ink`, middot separators.
- **Forms** — input: `--hair` border, `--radius`, serif or sans 16, focus border `--accent`; **primary button**: `--accent-ink` fill + white text (verify AA ≥4.5 on `#8f2a20`; the old `--ruby #c45a3b` fill failed), sans 12/600, `--radius`; **secondary button**: transparent + `--hair` border.
- **Archive rows / search results** — `--hair` top rule, 24px vertical padding, hover `color-mix(--accent 4%)`; date sans 12/600, headline serif 16–18/600, preheader `--ink2` 14.
- **Bias spectrum** (sources) — cell labels mono 10–11 `.08em`; active cell underline `--accent` (flat, not `--ruby-hover`); count serif 18/600 tabular-nums.
- **Factuality badges** (sources) — mono 10 `.08em` uppercase, **flat**, `--ok`/`--warn` text on tinted ground, AA-checked; not rounded pills.
- **Stats tables** — mono headers 10–11 `.08em` `--muted`; cells sans 14 with `font-variant-numeric: tabular-nums`; `--hair` row rules; `<th scope="col">` + `<th scope="row">` (a11y). Status cells via `--ok`/`--warn`/`--accent` + text.
- **Period toggle** (stats) — flat segmented control, sans 12, active = `--accent-ink` fill / white, hairline dividers.
- **Thread status badges** — mono 12 `.08em`, flat, `--ok` (active) / `--muted` (dormant/closed).
- **Feedback page** — bring its reduced `:root` up to the full token set; h1 serif 27/600; centred narrow measure is fine.

## CSS pitfalls (verify with getComputedStyle, not the eye)

- **Element selectors inside a container clobber component classes.** A rule like `.brief p { font-size:14px }` (specificity 0,1,1) silently overrides `.eyebrow`/`.srcs` (0,1,0) when those components are also `<p>` inside `.brief` — so should-know eyebrows/sources rendered 14px while must-know rendered 11/12px, invisibly. Fix: give the summary its own class (`.brief p.summary`) so container rules never target component elements by tag. General rule: **don't style by tag inside a container that also holds classed elements of that tag.**
- Confirm cross-tier consistency by reading `getComputedStyle().fontSize` on the actual elements (must-know vs should-know eyebrow, sources, bias) — reading the CSS values alone misses cascade collisions.

## Accessibility (WCAG 2.1 AA)

- Palette is AA-verified across both themes (contrast ratios documented at source in seanfloyd.dev base.css).
- In-text links carry a persistent underline — never colour-only (1.4.1).
- Visible `:focus-visible` outline in `--accent` on every interactive element.
- No colour-only meaning — bias is a text code + link, not a swatch; status is a labelled token, not a hue alone.
- **Heading tree**: exactly one `<h1>` per surface (the digest wordmark/date); section labels (Must Know / Should Know) are `<h2>`; story headlines `<h3>` in both tiers. No skipped levels. The visual size difference between must-know and should-know headlines is a class, not a heading-level, change.
- **Semantic HTML5 structure** (helps AT + Safari Reader's article heuristics): `<main>` holds the content; `<header>` (masthead) and `<footer>` are **siblings of** `<main>` (nested inside, they lose `banner`/`contentinfo` roles); each tier is a `<section aria-labelledby>` referencing its `<h2>`; each story is an `<article>` (both tiers); the date is a `<time datetime>`; nav rows are `<nav aria-label>`. (These earn their keep for screen readers + SEO — **not** for Safari Reader, which is structurally excluded for a digest regardless of semantics; see the Safari Reader note below.)
- **Decorative glyphs get `aria-hidden`**: the `■` dateline tick, the `文A` translate glyph, `←` back-arrow, and `/` dateline separators — they read as noise to a screen reader otherwise.
- **Link text is self-describing**: numbered per-source links carry `aria-label="{Source}, article N"` (bare "1/2/3" is meaningless to AT); each share `#` anchor's `aria-label` includes its headline (not a repeated "Link to this story").
- **Touch targets ≥24×24** (WCAG 2.5.8): the translate pill and back-link need real hit area — pad them.
- **Reading prose is one comfortable size** (body 18/20): must-know summary/lede, why-it-matters, reporting-varies, and should-know summaries all match. Never use font-size to signal importance/tier for *prose* — that just hurts readability; use headline size, labels, borders, and content depth instead. Only true metadata (sources, bias, eyebrows, labels) is small.
- **Safari Reader — structurally excluded, don't chase it** (verified against Apple's actual extracted Reader bundle, iOS 17.2). Reader is a *single-dense-prose-subtree* finder with hard gates: `ReaderMinimumScore = 1600`, `ArticleMinimumScoreDensity = 4.25` (score **per pixel** — kills tall/airy layouts), `TextNodeLengthPower = 1.25` (rewards *contiguous* prose superlinearly, so fragmented per-card summaries score poorly), a negative-class regex penalizing `footer|share|tags|link|subscribe|…`, and link-density pruning that deletes any node >0.5 link-text (>0.2 low-weight). It selects **one** subtree, never stitches sibling `<article>`s. A digest fails as a whole (density gate) and per-story (score gate); and when a single dense story *does* clear 1600 (summary+why+varies ≈ 700 chars), Reader renders **just that one story** and throws away the rest — a broken partial, not the digest. Making it eligible would mean merging stories into one long essay and stripping the source citations (the whole credibility mechanism) — self-defeating. **The right move: ship a deterministic reader-styled view on `circulation` (`/today` or a dedicated route)** that keeps citations + bias, instead of fighting a black-box heuristic. Source: the extracted Safari Reader JS (`ReaderArticleFinder.js`) + Mozilla Readability for the link-density formula.
- **UI-component contrast ≥3:1** (1.4.11): the translate pill border is solid `--accent` (a translucent `color-mix` border failed at ~2.2:1 and made the only affordance invisible).
- **`prefers-reduced-motion`**: guard the hover/focus anchor transitions (and any future motion) with a `reduce` block.
- Verified: Lighthouse accessibility **100**, zero failing audits (properly-shelled design); computed sizes cross-checked per tier.

## Chrome — validated build (2026-07-04)

Supersedes the "Circulation chrome" sketch above where they differ (esp. radius: **chrome is soft,
not flat-0**). All five pages built + Lighthouse-100 as mockups in `scratch/chrome-mockups/` (index
`chrome_v12`, `sources_v2`, `stats_v2`, `threads_v1`, `feedback_v1`) — those are the port reference.
The **digest** keeps its own sharper/flatter wire treatment and narrower reading measure; this
section is the **app chrome** (index/sources/stats/threads/feedback), a documented divergence
(editorial content vs. tool chrome).

**Canonical tokens (one shared `:root`, redefined under both `@media` and `[data-theme]`):**
`--paper #fafaf8/#16150f`, `--panel #fff/#1f1e16`, `--panel-2 #f4f3ee/#1a1912`, `--ink #191917/#eceae1`,
`--ink2 #3b3a36/#cbc9bd`, `--muted #6f6d65/#9d9a8e`, **alpha hairlines** `--line rgba(ink,.12)/rgba(paper,.13)`
+ `--line-strong .22/.24` + `--wash .035/.05` (one token pair works both themes over any bg),
`--accent #b1352a/#e2675b` (marks/live), `--accent-ink #8f2a20/#ec7d72` (text/links) + `--accent-wash`,
status `--ok #2f9b5e/#46c184` (+`--ok-ink`), `--warn #a8760c/#d9b34d` (+`--warn-ink`), bias
`--bias-l #5f7391/#8194b3` `--bias-c #928f86/#a5a196` `--bias-r #b0604e/#cf7a68`. Radius `--r-input:6px`
(inputs/buttons/segmented) `--r-card:8px` (cards); badges/pills full-round; everything else flat.

**Layout:** one **820px** column (`.col`), 28–32px page padding. Reflow to stacked below 560px.
The column *is* the measure — no per-element `max-width`s.

**Components (as built):**
- **Top bar** — nav links left (muted sans, middot sep), right cluster = `文A Translate` outline pill
  + `◐` theme toggle. Sub-page nav starts with `← Archive`.
- **Masthead** — brand eyebrow `Sean's Daily <em>Digest</em>` (serif 15, "Digest" in `--accent-ink`)
  over the page `<h1>` (serif 34/600), then a sub-row: mono kicker (left) + mono tabular stat (right);
  `2px solid --ink` bottom rule.
- **Raised segmented control** (period toggle / filters) — track = `--wash` + hairline + 3px pad;
  active = `--panel` pill + `--ink` text + tiny shadow. **Do not** fill the active segment with accent
  (white-on-accent is a legibility/reserve problem — this replaced it).
- **Issue-numbered running order** (index) — grid `72px 1fr auto`: `#219`/date · summary(preheader) ·
  bias-glyph + "N sources". Month dividers. Row = the digest's **preheader** (whole-issue summary),
  not a lead headline; sources bar = **whole-digest** spread. Hover wash; today row = `--accent-wash`.
- **Single spectrum bar** (sources balance) — 7 equal segments, populated = solid bias colour + count
  on the paper (never white-on-bar), empty fringe = 12–14% tint; labels below; caption states the
  ceiling. Used at large size on sources, compact (shipped-vs-catalog) on stats.
- **Factuality = neutral ordinal meter** (▮▮▮/▮▮▯/▮▯▯) + mono label — NOT green/amber. One colour
  system per screen: colour is spent on the bias spectrum, so factuality stays monochrome.
- **RAG health** (stats only — the one screen where the alarm axis belongs) — `✓/▲/▲/✕` glyph +
  `--ok`/`--warn`/`--accent` + the % (redundant, survives greyscale).
- **Table craft** — mono uppercase headers, serif source names, **mono tabular-nums numeric cells
  right-aligned**, alpha-hairline rows (no zebra), hover wash, each table in `overflow-x:auto` so wide
  tables scroll rather than break. Add feed *staleness* ("newest item") to health.
- **Stat items** — big serif number + mono caps label + sans sub; no heavy cards. Semantic colour
  (`--ok`/`--warn`/`--accent-ink`) only where it means something.
- **Roadmap tiles** — dashed border + faint warm tint + mono `Planned` badge + a note stating exactly
  what data each needs. Honest about un-instrumented metrics.
- **Thread markers** — ongoing = filled `--accent` dot (the legit "live" accent use); dormant/closed
  = hollow muted/faint (hue reserved, shape-coded).
- **Feedback** — no form (Yes/No removed); warm mailto CTA (primary button) + "especially useful to
  hear" list, narrow 560px measure.

**Steals applied** (see `docs/2026-07-04-chrome-design-research-and-language.md` + the `ui-design-craft`
dotfiles skill): Radix 12-step scale model (accent step-9, text 11/12 APCA-guaranteed — retires manual
AA-lock and fixes the dark filled-button bug via computed contrast text), alpha hairlines, table craft,
editorial issue-numbering, single-accent discipline, `tabular-nums`/`text-wrap:pretty`/`hanging-punctuation`.

**Verified:** Lighthouse a11y/BP/SEO 100 on all five (shell-wrapped); light+dark; ≥390px. Pitfalls hit
& fixed: opacity-dimming fails AA (recede by colour); text on mid-tone bias colours fails (numbers on
paper); a stray `</div>` full-bleeds the column (full-page wide screenshot after structural edits).

## Digest-view frame + thread detail (validated 2026-07-04, mockups `digest_v1` / `thread_detail_v1`)

Completes the surface set: the **digest as viewed on circulation** and the **thread detail page**
(clicked through from the thread index). Both Lighthouse a11y/BP/SEO 100, light+dark, ≥390px.
Mockups are Artifact fragments (start with `<style>`, no doctype/head/body — the Artifact wrapper
supplies those, same as the other five). Generator: `scratch/chrome-mockups/build.py` (job tmp copy).

### Theme toggle — 3-state, System default, localStorage (ALL seven pages)

Supersedes the earlier 2-state flip (which had no return-to-system and did not persist across
navigation). One shared implementation, back-ported into all five existing chrome mockups:

- **Cycles System → Light → Dark → System.** System is the default *and* a returnable state: picking
  it clears the stored key and live-follows the OS (`matchMedia('change')` listener).
- **Persistence = `localStorage['theme']`, never a cookie.** Rationale: persists across page
  navigations on the same origin; never sent to the server (no cookie-consent implication — a theme
  pref is strictly functional); cookies would only buy server pre-render, not worth the plumbing.
- **No-flash:** a synchronous inline `<head>` script reads `localStorage`/system and sets `data-theme`
  before first paint. In the port, circulation injects this like its other head injections.
- **Accessible:** the button shows a glyph **and** a word (`◐ System` / `☀ Light` / `☾ Dark`) and its
  `aria-label`/`title` announce current state + next action (a mystery cycling glyph is a UX weakness).
  `≥24px` hit area; word hides `≤420px` (glyph + aria-label remain). Functionally verified (Playwright):
  default=system, cycle order, localStorage persistence across reload, no-flash.

### Cross-page chrome contract (verify with getComputedStyle, not the eye)

The top bar is app chrome — it must be pixel-identical across every page, independent of the content
column width. Multi-file drift is invisible to the eye and to Lighthouse; measure it. Canonical values
(all seven pages match unless noted; harness: `scratch/chrome-mockups/` build + a computed-style probe):

- **Container:** `.wrap{padding:28px 18px 56px}` → `.col{max-width:820px;margin:0 auto}` → `.topbar`.
  Top bar sits flush to the column: `top=28px, left=140, width=820` at a 1100px viewport. **The digest
  is the one allowed width exception** (`.paper` 800px reading column → `left=190, width=720`), but its
  top bar still aligns to `top=28`.
- **Translate pill** (ONE spec everywhere — this was the drift Sean caught): `border-radius:999px;
  padding:5px 12px; border:1px solid var(--accent); background:transparent; font:12px/normal 400 sans;
  gap:6px; height≈29px`. The `文A` glyph is **serif** (`.g`/`.glyph{font-family:var(--serif)}`) — not
  mono. Beware inheriting the body's `line-height:1.6` (it silently makes the pill 2px taller and
  misaligns it with the toggle — set `line-height:normal`).
- **Theme toggle:** `border-radius:6px; padding:6px 10px; border:1px solid var(--line)`. The digest is
  the one exception (uses `--hair` solid, not `--line` alpha — its flat-wire severity; visually near
  identical). The right cluster `.topright{gap:12px}` (pill↔toggle spacing) — a 10px vs 12px slip here
  reads as "the pill looks off" even when the pill itself is pixel-correct.
- **Detail pages don't get a redundant back link.** The thread detail page uses the top nav ("Threads"
  is the return path), not a `← All threads` row — that stacked a third header level. Sub-pages =
  top bar → masthead, nothing between.
- **Nav:** sans 13px `line-height:normal` (NOT the body's inherited 1.6 — that inflates the link
  boxes and makes the whole top bar ~3px taller, pushing the masthead down), muted, middot separators,
  **no vertical padding on the links** (matches the reference; keeps nav height ≈16px so the pill/toggle
  at 29px drive the bar). Pattern: **sub-pages start with `← Archive`** then the sections; the current
  section is omitted (e.g. the threads index shows `← Archive · Sources · Stats`). **The thread *detail*
  is the exception — it keeps `Threads` in the list as the back-to-index path** (`← Archive · Sources ·
  Threads · Stats`), since it has no separate back link.
- **Narrow pages (feedback):** the chrome (top bar + footer) stays full 820; the content is capped
  (`max-width:620px`) **and left-aligned to the column edge, NOT centered** — a centered narrow block
  floating inside wide chrome reads as broken (content must share the chrome's left edge). This
  full-width-rules-over-a-narrow-measure layout is a *researched* editorial device (NN/g / NYT /
  Economist — "reads 'designed grid' more than any font choice"), not a compromise — see
  `2026-07-04-chrome-design-research-and-language.md` §5. Feedback uses the **standard nav** like every
  page; its "arrive-from-a-digest" context lives in the body CTA ("reply to today's issue"), not a
  bespoke top-bar back-link.
- **Footer:** one shared `.site-foot` (`display:flex;flex-direction:column;gap:6px`; a `.row` of
  middot-separated links + one contextual `<p>`; `margin-top:44px`, `border-top` alpha hairline,
  `line-height:normal`). The chrome pages all use it; the **digest keeps its own document footer** (the
  same deliberate divergence as its `--hair`/sharp severity — it's the *document's* footer, not app
  chrome). Footer *content* (which links, the `<p>` line) is page-contextual; the *structure* is shared.
- **`--serif` token identical everywhere** (`"Source Serif 4", Georgia, "Times New Roman", serif`) and
  every toggle carries `min-height:24px` (touch-target floor).

### Search results page (`search_v1`) + Index interactions

**Search results (`/search`, mockup `search_v1`).** Same chrome shell (top bar / masthead / footer).
Masthead `<h1>` "Search the archive" + an editable search field (serif/sans 16, `--panel` bg,
`--line-strong` border, `--r-input`) + a filled accent-ink Submit. Below a 2px rule: a mono result
**count** (`N results for "…"`), then a `<ol>` of result rows. Each row is a two-column grid
(`120px 1fr`): left = mono **date** over a **tier** micro-label (`MUST KNOW` in `--accent-ink` /
`SHOULD KNOW` muted); right = the serif **headline**; whole row links to that day's digest, hover
`--wash`. This mirrors the FTS output exactly (`headline`, `tier`, `date → /{date}`) — no fabricated
source/snippet columns (they don't exist in the query). Empty/no-query states: a muted line, not a card.

**Index interactions (`chrome_v12`).** The archive is ~200 issues over 7 months (not 219), growing
~1/day. MVP is **client-side over the server-rendered list** — no new endpoints:
- **Recent / This year / All segment** — the raised segmented control filters the already-rendered
  `<li class="issue">` rows by a `data-year` attribute (Recent = the newest ~15; This year = current
  year; All = show all). Pure JS over loaded rows.
- **Date jump** — a native `<input type="date">` ("Jump to date"); on change, navigate to `/{value}`.
  A native picker *is* the calendar — no custom widget, fully accessible. (v2: restrict selectable days
  to dates that have an issue via a `/dates.json`; MVP allows any date and 404s the misses.)
- **No month accordions, no numbered pagination** (research §5 leans editorial running-order; pagination
  is clunky for a reverse-chron feed). Server-side `/archive.json` load-more is deferred until the
  archive is genuinely heavy — not at 200 rows.
- **No separate "Browse the full archive" link** — the segment's **All** is the show-everything; the list
  ends with a **Load more** affordance (secondary outline button) that pages older issues via
  `/archive.json`. (Resolved 2026-07-04 — the earlier dual "All" + "browse archive" was redundant.)

**Enforce with the diff, not the eye.** `scratch/chrome-mockups/cssdiff.js` reads `getComputedStyle`
for every shared chrome element across all pages and reports only diverging properties (`node
cssdiff.js`, or `node cssdiff.js .pill`). It ignores inherited font props on textless containers
(false positives) and tags the digest's own-token/width values `· digest` rather than `DRIFT`. Current
state: **✓ no cross-chrome drift.** Computed-value match ≠ visual match — it also caught gap/line-height/
glyph-font slips a naive metric set misses, so pair it with a screenshot after structural edits.

### Digest-view page (`/{date}`) — sharp body in a soft frame

The digest body is a **frozen HTML blob** from `newsroom/render.py`; circulation only injects
head/nav/footer chrome around it. The redesign wraps the validated dispatch body (unchanged — it keeps
its own token set `--bg`/`--hair` and flat-wire severity) in the chrome frame:

- The chrome **top bar replaces the digest's own `.toputil`** (do not stack both): nav
  `Archive · Threads · Sources · Stats` (muted sans) left; `文A Translate` pill + theme toggle right.
  It is nav+controls only — the digest's *own* masthead stays the page masthead, so **no double
  masthead** (mirrors how the chrome set separates top-bar from masthead).
- The **one toggle drives both** the frame and the digest body (`[data-theme]` overrides already exist
  in the blob) — this gives the digest the manual toggle it lacked.
- Top-bar CSS is styled from the **digest's own tokens** (solid `--hair` hairline), not the full chrome
  token set — the thin bar needs nothing more. The sharp-body / soft-frame boundary is deliberate.

### Thread detail page (`/thread/{id}`) — the evolving story tracker

Server-rendered Rust, fully yours to redesign; uses the **chrome token family** + 820px column (matches
`threads_v1` index). Surfaces the thread data model richly (`threads`, `thread_installments.content`
JSON, `thread_questions` — all populated in prod: 176/269/445 rows):

- **Masthead:** brand eyebrow `Sean's Daily Digest` → status row (status marker + mono span
  `Tracking N days · N updates · since DD Mon`) → `<h1>` = thread label → **"the story so far"**
  standfirst (serif muted; mono `THE STORY SO FAR` lead-in). 2px `--ink` rule.
- **Status marker** (matches the index): ongoing = filled `--accent` **live-dot** (the one legit accent
  use); dormant = hollow `--muted` ring; closed = hollow `--muted` square. Shape-coded, hue reserved.
- **"Still watching" ledger** — the distinctive element, placed high (forward-looking hook): a
  `--panel` card with a **`3px --accent` left border echoing the digest's why-it-matters mark**, mono
  `STILL OPEN` label, open questions as a serif list with hollow-ring bullets. From `thread_questions`
  where `status='open'`.
- **"How it developed" timeline** — reverse-chron `<ol>` with a `--line` spine + node per update
  (filled `--muted` = substantive, hollow = quiet day). Each update: mono `when` row (date · `day N` ·
  `→ in the DD Mon issue` link to `d.date`) → that day's **headline as it appeared** (`cluster_story`,
  shows framing drift) → **what's new** facts (top-3 `whats_new`, each with numbered **source** links)
  → inline `qnote`s: `ANSWERED` (`--ok`, from a resolved question) / `NEW QUESTION` (`--muted`).
- **Quiet days recede by muted colour** (italic muted headline), not opacity (banked AA lesson).
- **PORT data notes:** (1) "story so far" has no persisted thread-level field — reuse the latest delta
  or add a synthesis field. (2) fact sources cite opaque article IDs (`A185`) — resolve to outlet/URL
  via `article_index`/`shown_narratives` (mock uses placeholder outlets). (3) the page has **no OG tags
  today** — add `og:type=article` + title=label + description=story-so-far (threads are shareable).

## Token source of truth + production delivery (port architecture, Path B)

The whole system is **static — it compiles into the Docker image; nothing needs runtime computation**
(tokens are CSS custom props the browser resolves; theme is `data-theme`+`@media`; email's literal-hex
inlining is a build-time transform of static values). The real problem is that there is **no single
source of truth** today (hexes copy-pasted into `newsroom/digest.css`, six `:root` blocks across
circulation `*.rs`, and email inlining → documented drift). The seed fix landed this session:

- **`design/tokens.css`** — one canonical token file (shared core + chrome additions + digest `--hair`),
  with `@media dark` + both `[data-theme]` blocks. This is the CODE source of truth (this doc stays the
  human one). The mockups' inlined tokens are generated from it.
- **Circulation (Rust):** `include_str!("../../design/tokens.css")` — baked into the binary at compile
  (precedent: `sources.json` via `include_str!`, build context already repo-root). Collapses the six
  duplicated `:root` blocks. Bonus: real lintable `.css` vs escaped Rust strings.
- **CSS delivery = per-page INLINED, composed from the shared source at render** (NOT one external
  `app.css`). This is the Lighthouse-optimal choice: an external shared sheet is render-blocking *and*
  trips "Reduce unused CSS" per page; inlined critical CSS is exactly Google's guidance and is why the
  mockups hit 100. DRY at the source, inline at the output.
- **Cache-busting applies to fonts, not CSS** (inlined CSS rides the HTML cache; there's no file to
  bust). Fonts are the target: `include_bytes!` the woff2, serve at a **content-hashed path**
  `/assets/fonts/source-serif-4.{sha}.woff2` with `Cache-Control: public, max-age=31536000, immutable`
  (Rust computes the short SHA of the included bytes at startup, builds the route + `@font-face src` to
  match; upstream already does this — `source-serif-4-latin.2a24bad4.woff2`). Do **not** base64-inline
  fonts in production (the mockups only do it for Artifact self-containment); the digest blob's web view
  should reference the same hashed font URL so all seven surfaces share one cached download.
- **Newsroom (Python):** read `tokens.css` at render; `resolve_css_variables` inlines to literal hex for
  email from this one source. **Digest blob is the exception** to per-page-external — it stays
  self-contained (email needs inlined/var-resolved CSS; web keeps the hashed-font reference).
- **Parity canary test** asserting both consumers resolve identical hex (self-expiring).
