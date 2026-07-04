# Chrome design: research findings + emerging design language

Captured 2026-07-04, during the Dispatch → chrome redesign exploration. Source: five
web-research digs (colour/comprehension, icons/scannability, modern-vs-dated, product
design-system steals ×2, editorial devices) run as subagents, plus the interactive mockups
(`chrome_decisions` v1, `chrome_v2` evidence, `chrome_v3` archive). This doc is the durable
record so the findings don't stay trapped in one session's context. The transferable
principles are also being extracted into a **generic** design skill in dotfiles; this file
keeps the news-digest-specific application.

## 1. Colour for scannability & comprehension

- **Colour is preattentive** — found in ~200-250ms across the whole display, search time
  roughly flat as row count grows (vs linear for shape/position). Strong, old evidence
  [Healey & Enns; InfoVis-Wiki].
- **The win is redundant coding, not colour.** Colour + shape-distinct icon + text ≈ **88%**
  selection accuracy vs **66%** colour-alone, **58%** shape-alone [Nothelfer et al.,
  "Redundant Encoding in Data Visualizations"]. Redundancy also blunts the density penalty
  and covers the CVD population.
- **One alarm axis per screen.** People distinguish only ~5-7 categorical colours; for status
  you want ≤3-4. Multiple colour axes compete for salience → nothing pops [NN/g; gov.uk].
- **Green/amber/red (RAG) is near-global for *operational* status** (traffic-light), safe for
  feed-health. Cultural colour *symbolism* varies but the *signalling* convention is universal.
- **Binding constraint = CVD (~8% of men, ~0.5% of women).** Red/green is maximally hostile to
  the commonest deficiency; WCAG 1.4.1 (Level A) forbids colour as the only channel. Every
  status must survive greyscale (icon + label) [Colour Blind Awareness; W3C WAI].

**Applied here:** RAG colour for **feed-health only**. **Factuality** = ordinal (shape meter,
no alarm colour — a green "very high" reads as a verdict and fights the health axis).
**Thread-status** = shape/weight, hue reserved. All status = icon + colour + text.

## 2. Icons for scannability & comprehension

- **Icon + label, never icon-only.** Unlabeled icons are routinely misread; ISO 9186 sets a
  ~67% comprehension bar many fail; adding the word "MENU" lifted clicks ~20% [NN/g; ISO 9186].
- **Triple redundancy is the design-system consensus** — Carbon defines a state as "the sum of
  color, shape, and symbol," text label "the most important element"; Polaris/Primer/Material/
  Lightning all pair icon+colour+text with visually-hidden text for a11y.
- **Tiny, non-decorative set.** ~4 semantic glyphs (check / triangle / circle-x / info).
  Decorative/ambiguous icons measurably *raise* cognitive load (EEG study, PMC11142986).
- **A bare coloured dot is colour-only** → insufficient for actionable status; reserve the dot
  for ambient presence (online/unread), use a shape-distinct glyph the moment severity matters.

**Applied here:** health = `✓ Healthy / ▲ Degraded / ✕ Down` (shape-distinct + colour + word);
factuality = 3-bar ordinal meter + word; threads = filled/outline mark + word.

## 3. Modern vs dated (radius / depth / density)

- **0px is NOT inherently dated** — sharp/flat is a live, respected editorial/Swiss camp
  (Vercel marketing, brutalist). It reads *un-designed* only when flatness pairs with cramped
  rows + heavy borders + default web serifs. "Dated" is the *ensemble*, not one token.
- **Georgia/Times as body is a genuine dated tell** — and was skewing our Artifact previews
  until we embedded real Source Serif 4.
- **Modern radius band:** respected systems cluster at **6px** inputs/buttons, **8px** cards,
  **full-pill** badges. Vercel/Geist restricts to 4/6/9999; Tailwind `rounded-md`=6px; Polaris
  4-8px; Radix contextual 3-8px [vendor docs]. A timid 3px is neither committed-sharp nor
  modern-soft — drop it.
- **Flat is over; subtle depth is back** — NN/g measured weak-signifier flat UIs at +22% time /
  +25% fixations; the modern move is a **hairline border + one soft low-opacity shadow**, never
  bevels/gloss. Use sparingly (a calm tool leans on borders, not shadows).
- **Spacious, not cramped.** "Start with too much whitespace"; data tables get *taller rows +
  generous padding*, not tiny type [Refactoring UI].
- **The type-role split is THE anti-newspaper move** — serif for display/headlines, clean
  sans/mono for chrome/labels/data.

**Decision (working):** chrome adopts the **soft modern-editorial** band (6/8px + one subtle
elevation + full-pill badges); the **digest** keeps its sharper wire severity. A documented,
coherent divergence — editorial *content* vs. app *chrome* — not an inconsistency.

## 4. Design-system steals (tables, badges, focus, tokens, dark mode)

Ranked, best first. All vendor-published unless flagged.

1. **Radix 12-step colour scale** as the token model. Fixed semantic rungs: 1-2 app bg, 3-5
   component bg (3 rest / 4 hover / 5 selected), 6-8 borders (6 separator / 7 element / 8
   strong+focus), 9-10 solid fill (9 = the *one* pure accent), 11-12 text (11 secondary /
   12 primary) with **APCA-guaranteed contrast**. Matching **alpha** variant per step composites
   over any surface. Dark mode is NOT lightness-inverted: 9/10 stay ~fixed, backgrounds keep a
   whisper of hue, raised surfaces get *lighter*. [radix-ui.com/colors] — **This retires the
   manual "AA-lock at port" problem** and fixes the recurring dark-mode white-on-coral button bug
   (filled accent = step 9 + computed `accent-contrast` text, not hardcoded white).
2. **Alpha hairlines** — borders/dividers as translucent ink (`rgba(0,0,0,.08)` light /
   `rgba(255,255,255,.08)` dark, or Radix `grayA6`). One token pair works in both themes over
   any bg. [Geist gray-alpha; Raycast .08/.16]
3. **Two-layer focus ring** `box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent)` — white/bg
   gap ring + accent ring, legible over any surface with no dark override (verify accent-on-bg
   ≥3:1). [Geist] Linear's calmer single 2px @50% is the fallback.
4. **Badge variants** — Radix `soft` = accent-A3 bg + accent-A11 text (AA-guaranteed) as the calm
   default; **Primer transparent hairline-pill Label** (no fill = no contrast pair to fail) as the
   most editorial tag; `solid` (step 9) reserved for one urgent flag. [radix Badge / Primer Label]
5. **Table craft** — 44px rows (size-2), 12px cell padding, **alpha-hairline dividers, no zebra**,
   `tabular-nums` right-aligned on numerics, a subtle **hover wash** (~10% alpha, not a solid
   swap), two density tiers (comfortable 40 / compact 32), sticky header. [Radix Table / Primer
   DataTable / Carbon / Polaris]
6. **Border folded into the shadow** — cards use one `box-shadow` = crisp 1px edge layer + 1-2
   soft blur layers, not separate `border`+`box-shadow`. [Radix + Primer + Geist]
7. **Dark mode = mute the accent (not the greys), keep a whisper of hue in the near-black bg
   (e.g. `#181111`/`#16150f`), raised surfaces step *lighter* by small fixed deltas.** [Primer
   `dark_dimmed`; Carbon; Stripe `#14171D`/`#2B3039`/`#C9CED8`]
8. **Split UI/label type scale from the reading scale** — serif reading scale stays generous;
   a separate tighter sans/mono scale for nav/cells/timestamps/badges. [Carbon / Geist]
9. **Radix space (4/8/12/16/24/32/40/48/64) + radius (3/4/6/8/12/16) scales** — adopt verbatim
   (matches our existing 4px grid).
10. **Motion tokens** 150/200/300ms, one easing; nothing <150 (jittery) or >300 (laggy). Radix
    uses *no* transitions at all (calm) — pick one philosophy. [Geist / Polaris]
11. **Calm empty/loading** — Radix's 1s two-step alpha pulse skeleton (no shimmer sweep); Primer
    Blankslate / Polaris EmptyState = one calm sentence + whitespace, no merchant-y illustration.

**Honest skips:** zebra striping; Carbon's 5-tier density + 300-weight display; shadow-based dark
elevation; uppercase institutional badges; cartoon empty states. **Do-not-fabricate:** Stripe's
exact focus-ring CSS and internal Dashboard table metrics are not public (community values are
folklore); Söhne not "Wremena" is Stripe's face. Linear/Raycast px/hex are third-party-scraped —
spot-check in devtools before hardcoding.

## 5. Editorial devices

- **Enforce three type roles** (serif=Story, mono=UI/metadata, sans/masthead) with *no bleed* —
  a dateline is always mono, a headline always serif. The single highest-leverage move; mostly
  discipline/deletion. [Rest of World style guide]
- **Wide full-width hairline over a narrow ~60-68ch measure** — rules span the grid, prose stays
  in a narrow column. Reads "designed grid" more than any font choice. [NYT/Economist fronts]
- **Reframe each digest as a numbered *issue*** (`No. 219 · Vol.`) + a numbered running order —
  turns "a digest" into a publication, using `digest_runs` data already stored. Strong, cheap,
  and it's our differentiator.
- **Single-accent discipline** (Economist red) — accent only on links-on-hover / live signal /
  word-splash, never on rules or labels. A *subtractive* steal.
- **Invisible micro-typography** — `tabular-nums` + `text-wrap: pretty` (body) / `balance`
  (headlines) + `hanging-punctuation: first last` (Safari, degrades). Zero downside.
- **Restrained motion** — hover link-underline draw (0→100% `background-size`, not
  `text-decoration`); optional sticky mono section label on long pages. Nothing with a canvas.

**Avoid (AI-default "warm editorial" cliché):** cream `#faf3e8` + terracotta as the *whole*
palette (already sensed — we cooled the paper to `#fafaf8` and made terracotta rare);
drop-caps + pull-quotes (need long prose a briefing lacks); decorative dividers stacked with
either; pure `#000`/`#fff` dark mode for reading.

## 6. The chrome design language (synthesised)

- **Palette:** seanfloyd.dev tokens + a terracotta ramp built on the Radix 12-step model
  (accent = step 9, text = 11/12 AA-guaranteed). Alpha hairlines. Cool paper `#fafaf8` / warm
  near-black `#16150f`. Semantic status axis (`--ok`/`--warn`/accent) **separate** from the brand
  accent, RAG on health only.
- **Type:** Source Serif 4 (400-600) display+body; system sans for UI; ui-monospace for tracked
  labels/datelines/data. Three roles, no bleed.
- **Radius/depth:** soft modern band — 6px inputs/buttons, 8px cards, full-pill badges, one
  subtle elevation (chrome only; digest stays sharp).
- **Status:** icon + colour + label everywhere; ordinal factuality meter; shape-coded threads.
- **Tables:** issue-numbered running order; 44/32px density; alpha hairlines; hover wash;
  tabular-nums.
- **Motion:** 150/200/300ms one ease, or none; hover underline-draw; `prefers-reduced-motion`.

## 7. Open decisions

- **Radius route:** soft (recommended, working default) vs committed-sharp — Sean to confirm.
- **Archive layout:** issue-numbered running-order list (mockup) vs the current collapsible month
  accordions — reconcile (accordions help long-list mobile; the numbered list is more editorial).
- **Mobile:** the running-order needs a stacked reflow < ~520px (in progress).
- Port sequencing still A (finalize digest reference) → C (mock remaining chrome pages) → B (port
  plan) → implement, per the handover.

## Sources

Full per-claim URLs are in the session's research syntheses. Primary anchors: Nothelfer et al.
(redundant encoding), NN/g (icon usability, flat design), W3C WAI 1.4.1, gov.uk Design System,
ISO 9186, Radix Colors/Themes docs, Vercel Geist, GitHub Primer, IBM Carbon, Shopify Polaris,
Rest of World style guide, Refactoring UI.
