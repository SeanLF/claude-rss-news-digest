# CSS standards (mid-2026)

> As of 2026-07. Verify current Baseline status (web.dev/baseline, MDN, caniuse) before relying on anything below — support windows move.

This repo: **no CSS framework**. Hand-authored, per-page inlined CSS composed from a `tokens.css`
design-token source. Dark mode via `prefers-color-scheme` + a data-attribute theme toggle. A
cross-page chrome consistency contract (same header/nav/footer everywhere). Optimize for: readable
at 2am, i18n-ready, theme-correct both ways, no specificity wars.

## Safe to use mid-2026 (Baseline: newly or widely available)

- **Custom properties** (`--x`, `var()`) — universal for years. The token substrate. Use freely.
- **Native nesting** (`&`) — Baseline (all engines since 2023). No Sass needed for nesting. Keep the `&` explicit; prefer it over bare nested selectors for clarity.
- **`:has()`** — Baseline. Relational/parent selector. Great for "style container based on child/state" without JS.
- **Container queries** (`@container`, `container-type`) — Baseline since ~2023. Prefer over viewport media queries for component-level responsiveness. Style queries (`@container style(...)`) are newer — check before relying.
- **Cascade layers** (`@layer`) — Baseline. The specificity-war killer. Order layers explicitly (e.g. `@layer reset, tokens, base, components, utilities;`) so load order and source order stop mattering.
- **`color-mix()`** — Baseline (settled 2023). Blend/tint/alpha-adjust tokens: `color-mix(in oklch, var(--fg) 12%, transparent)`.
- **`oklch()` / OKLCH color space** — Baseline; recommended default for new palettes. Perceptually uniform lightness → predictable tints/shades and better dark-mode ramps than HSL.
- **Logical properties** — Baseline. `margin-inline`, `padding-block`, `inset`, `border-inline-start`, `inline-size`/`block-size`. Use these by default (i18n / RTL-ready) instead of `left/right/top/bottom/width/height`.
- **Subgrid** (`grid-template-*: subgrid`) — Baseline (settled 2023). Align nested grids to the parent's tracks.
- **`:is()` / `:where()`** — Baseline. `:where()` has **zero specificity** — ideal for resets and low-priority defaults the toggle/theme must override.
- **`@property`** — registered custom properties (typed, animatable, with fallbacks). Baseline in the modern-browser set; verify if targeting laggards. Enables animating gradients/custom props.
- **`light-dark()`** — Baseline newly available (all engines since ~May 2024; "widely available" ~late 2026). Pairs with `color-scheme`. Good fit for token theming — but see the toggle caveat below.
- **Same-document View Transitions** — Baseline newly available (Firefox 144 completed the set). Safe for in-page transitions.
- **Cross-document View Transitions (MPA)** — **NOT Baseline yet** (Firefox still shipping as of mid-2026; Chromium 126+/Safari 18.2+ only). Treat as progressive enhancement; the site must work without it.
- **★ Anchor positioning** (`anchor()`, `position-anchor`, `@position-try`) — **Baseline 2026** (all engines: Chrome 125+, Firefox 132+, Safari 18.2+; `@position-try` flip wants Safari 18.4+). The 2026 flagship: native, no-JS tethering of tooltips/menus/popovers to any element, with automatic fallback positioning when they'd overflow. **Replaces Floating UI** and pairs directly with the Popover API — reach for this instead of a JS positioning lib.
- **`@scope`** — Baseline newly available (Jan 2026). Native scoped styling with donut-scope (`@scope (.card) to (.content)`) — style a subtree without leaking out and without specificity hacks. Serves the "no specificity wars / cross-page chrome contract" goal alongside `@layer`.
- **`text-wrap: balance`** (Baseline 2024) / **`text-wrap: pretty`** (Chrome 117+/Safari 26; **not Firefox** as of mid-2026) — cheap typographic polish. Use `balance` on headlines to even out ragged lines; `pretty` (fixes orphans/rivers on body text) as pure progressive enhancement.
- **Scroll-driven animations** (`animation-timeline: scroll()` / `view()`) — **progressive-enhancement ONLY** (Firefox still behind a flag mid-2026). Never gate content or essential motion on it; always gate behind `prefers-reduced-motion` too.

## Prevailing philosophy / idioms

- **Design tokens = custom properties.** Single source (`tokens.css`): color, space, type scale, radii, shadows. Everything references tokens, nothing hardcodes values.
- **No framework, hand-authored.** Native CSS now covers what Sass/utility-frameworks gave us: nesting (native), variables (custom props), color functions (`color-mix`/relative color), specificity control (`@layer`). Reach for those before adding a dependency.
- **Semantic + minimal classes.** Style by element/semantics where sensible; small, meaningful class set over utility-class bloat. No `mt-4 px-2 text-sm` soup.
- **Fluid type/space with `clamp()`.** `font-size: clamp(1rem, 0.9rem + 0.5vw, 1.25rem)` — fewer breakpoints, scales smoothly. Keep a `rem`-anchored min so it respects user zoom.
- **`color-scheme` + theme tokens.** Set `color-scheme` so form controls/scrollbars match. Theme by swapping token *values*, not by rewriting component rules.

## Best practices to default to

- **Theme both ways so the toggle always wins.** Define light and dark token values under BOTH `@media (prefers-color-scheme: dark)` AND an explicit `:root[data-theme="dark"]` / `[data-theme="light"]` selector. The data-attribute (toggle) must override the media query in *both* directions — a user forcing light on an OS-dark device, and vice versa. Note: bare `light-dark()` keys off `color-scheme` only and does **not** by itself respect a `data-theme` override — if you use it, drive `color-scheme` from the data attribute too, or theme via token swaps instead.
- **Logical properties for i18n** — default to `-inline`/`-block`; reserve physical props for genuinely physical needs (e.g. a fixed drop shadow direction).
- **`prefers-reduced-motion`** — gate all non-essential motion (view transitions, scroll animations, transitions): `@media (prefers-reduced-motion: reduce) { *, ::before, ::after { animation-duration: .01ms !important; transition-duration: .01ms !important; } }` (the rare justified `!important`).
- **Accessible focus** — visible focus for keyboard users: `:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }`. Never `outline: none` without a replacement. Ensure the ring has contrast in both themes.
- **Avoid specificity wars via `@layer`** — put resets/defaults in low layers, components above, utilities highest. Lets you keep selectors flat and readable.
- **Respect user zoom** — size type/spacing in `rem`/`em`; keep `clamp()` minimums in `rem`.
- **Contrast** — target WCAG AA (4.5:1 body, 3:1 large/UI). This project ships an AA palette; verify tints from `color-mix()` still clear the bar in both themes.

## Notable changes, last ~2 years

- Nesting, `:has()`, container queries, cascade layers, subgrid, `color-mix()`, OKLCH all crossed into Baseline (2023–2025) — the "modern CSS" set is now production-safe without polyfills.
- `light-dark()` and typed `@property` reached the modern set (2024–2025).
- Same-document View Transitions became Baseline (Firefox 144, 2025); cross-document still trailing.
- **Anchor positioning reached Baseline (2026)** — the biggest single win: JS positioning libraries (Floating UI) are now optional for tooltips/menus/popovers.
- **`@scope`** reached Baseline newly available (Jan 2026) — native scoped styling to complement `@layer`.
- Practical effect: Sass/PostCSS are now optional, not assumed. Native features replace most preprocessor jobs.

## Pitfalls / anti-patterns to avoid

- **Color-only status.** Never encode state (success/error/tier) in color alone — pair with icon, text, or shape. Fails colorblind users and dark-mode contrast.
- **px-locked layouts.** Avoid `px` for type/spacing/breakpoints; it defeats zoom and fluid scaling. Use `rem`/`clamp()`/container units.
- **Over-nesting.** Native nesting tempts deep trees → fragile specificity and unreadable rules. Keep to ~2–3 levels; flatten with `@layer` instead of nesting for override control.
- **`!important`** — a smell that you've lost the cascade. Fix with layers/`:where()`. Only justified exception: the reduced-motion kill-switch.
- **Glassmorphism / low-contrast chrome** — frosted-glass, white-on-translucent, faint grey-on-grey. Fails contrast and reads as "nobody checked." No white-on-frosted-glass (house rule).
- **Physical properties by habit** — `margin-left`/`width` where `margin-inline`/`inline-size` would be i18n-safe.
- **Theming only via `prefers-color-scheme`** — leaves the toggle unable to override the OS. Always pair with the data-attribute selector.
- **Relying on cross-document View Transitions as if Baseline** — it isn't (mid-2026); must degrade cleanly.
