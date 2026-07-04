# HTML Standards Reference (mid-2026)

> As of 2026-07: this reflects a point-in-time snapshot. Verify support/status on MDN + caniuse/Baseline before relying on anything marked "new" or "recent" — evergreen browsers move fast.

Scope: hand-authored semantic HTML for the `circulation/` web server AND HTML email, WCAG 2.2 AA target, HTML-over-the-wire fragments with progressive enhancement. Email and web have **different rules** — flagged inline as [WEB] / [EMAIL].

---

## 1. Semantic structure

- One `<main>` per page; wrap the primary content, exclude repeated chrome. One `<h1>` per page (the page's subject).
- Landmarks: `<header>`, `<nav>`, `<main>`, `<footer>` at page level; `<article>` for a self-contained item (a digest story, an archive entry), `<section>` only when it has a heading. Don't wrap everything in `<section>` — that's `<div>` with extra steps.
- Headings describe outline, not size. Never skip levels (h1→h3). Style with CSS, not by picking a bigger tag.
- Multiple same-type landmarks (e.g. two `<nav>`) need distinguishing `aria-label` ("Primary", "Archive by month").
- Lists: `<ul>`/`<ol>` for any repeated set (nav items, story lists, source lists). The HTML-over-wire `<li>` fragments MUST be valid `<li>` — the server returns them to append into an existing `<ul>`/`<ol>`.
- `<time datetime="2026-07-04">` / `datetime="2026-07-04T10:25:00Z"` for machine-readable dates; put human text as the child.
- `<figure>` + `<figcaption>` for images/quotes with a caption that's part of content. Plain decorative images don't need it.
- `<address>` for contact/authorship only; not for arbitrary postal addresses.

## 2. Accessibility (WCAG 2.2 AA)

- WCAG 2.2 AA is the live baseline (ISO/IEC 40500:2025; referenced by EAA, EN 301 549, Section 508). Backward-compatible with 2.1/2.0. WCAG 3.0 is still a draft — do not target it.
- Contrast: 4.5:1 body text, 3:1 large text (≥24px, or ≥18.7px bold) and UI/graphic components. Verify with a checker, not by eye.
- **ARIA sparingly** — "no ARIA is better than bad ARIA". Prefer a native element (`<button>`, `<a>`, `<nav>`, `<label>`) over a `<div>` + role. First rule of ARIA: don't use ARIA if HTML can do it.
- Every control has an accessible name: `<label for>` (or wrapping `<label>`), or `aria-label`/`aria-labelledby`. Icon-only buttons need `aria-label`.
- Name/role/value: interactive things expose all three. Native elements do this for free; custom widgets must maintain state (`aria-expanded`, `aria-pressed`, `aria-current`) yourself.
- Focus: visible focus indicator always (never `outline:none` without a replacement). WCAG 2.2 adds **Focus Not Obscured (AA)** — focused element must not be fully hidden by sticky headers/footers.
- Skip link: `<a href="#main" class="skip-link">Skip to content</a>` as first focusable element, visible on focus, targeting `<main id="main">`.
- Keyboard: everything operable without a mouse, logical tab order (DOM order = visual order; avoid positive `tabindex`).
- `aria-live="polite"` for async status/injected content the user should hear (e.g. "translated", vote result). Use `assertive` only for urgent. Container must exist in DOM before content is injected.
- `prefers-reduced-motion`: gate non-essential animation/transitions behind `@media (prefers-reduced-motion: no-preference)`.
- WCAG 2.2 new AA criteria to know: **Target Size (min 24×24 CSS px)**, **Dragging Movements** (provide single-pointer alternative), **Consistent Help** (help in consistent location), **Redundant Entry** (don't re-ask info), **Accessible Authentication** (no cognitive-test-only login).
- `lang` on `<html>` (and on any inline foreign-language span, e.g. translated content — set `lang` on the swapped region).

## 3. Modern elements/attrs worth knowing

- `<dialog>` + `.showModal()` — modal dialogs with built-in focus trap, `Esc`-to-close, backdrop (`::backdrop`), top-layer. Baseline widely available.
- **Popover API** (`popover` attr + `popovertarget` on a button) — light-dismiss overlays, no JS. Baseline **"newly available" (Jan 27 2025)** — treat as newly, not widely, available. (The April 2024 "all engines" announcement was retracted over a Safari iOS light-dismiss bug; fixed in iOS 18.3, which is when it actually crossed Baseline.) Good for menus/tooltips/disclosure; pairs with CSS anchor positioning. For older floors, keep a JS fallback.
- **Invoker Commands** (`command`/`commandfor` on `<button>`) — declarative open/close for dialog + popover, no JS. Reached Baseline across all major browsers ~early 2026 (Safari 26.2 completed it). Newer than popover — verify your target floor before relying without a JS fallback.
- **Interest Invokers** (`interesttarget` on `<a>`/`<button>`) — the 2026 successor to Invoker Commands for *hover/focus*-triggered UI (tooltips, hover-cards) with built-in a11y timing and touch/keyboard equivalence. Very new (forward pointer, not Baseline mid-2026) — track it, don't ship without a fallback.
- **Customizable `<select>`** (`appearance: base-select` + `<selectedcontent>`, styleable `<option>`s) — style a native select's listbox/button with CSS, no JS widget, keeping full a11y/keyboard/form semantics. Chrome 135 stable + Safari 27; Firefox Nightly only; **NOT Baseline** (mid-2026) — gate with `@supports (appearance: base-select)` so it degrades to the native control. Could style the digest's translate-language `<select>` natively.
- `<details>`/`<summary>` — native disclosure, zero JS, fully accessible. Ideal for progressive-enhancement collapse (FAQ, "why it matters" expanders).
- `loading="lazy"` on `<img>`/`<iframe>` below the fold; `loading="eager"` (default) above. Universally supported. [WEB only — not for email.]
- `fetchpriority="high"` on the LCP image/critical resource, `"low"` for deprioritized; unknown-safe (ignored if unsupported) — pure progressive enhancement. [WEB]
- `inert` attribute — makes a subtree unfocusable/unclickable/hidden from AT. Baseline widely available. Use for background content behind a custom overlay (native `<dialog>.showModal()` handles this automatically).
- Form validation attrs: `required`, `type="email"`, `pattern`, `min`/`max`, `minlength`/`maxlength`, `autocomplete` (token list — big a11y + UX win), `inputmode`. Prefer native constraint validation before JS.

## 4. Best practices

- **Progressive enhancement**: HTML works without JS; JS enhances. The `<li>`-fragment pattern degrades to full-page nav when JS is off — keep the underlying links/forms real.
- **Real links and buttons**: `<a href>` navigates, `<button>` acts. Never `<div onclick>`/`<span onclick>` — those lose keyboard, focus, semantics, and middle-click/open-in-new-tab.
- Links need discernible text ("Read more" alone fails); describe the destination.
- `<head>` essentials [WEB]: `<meta charset="utf-8">`, `<meta name="viewport" content="width=device-width, initial-scale=1">`, `<title>`, `<meta name="description">`, canonical `<link rel="canonical">`, OG/Twitter tags (`og:title`, `og:description`, `og:image`, `og:url`, `og:type`), favicon.
- Responsive images [WEB]: `srcset` + `sizes` for resolution/art-direction; `<picture>` for format fallback (AVIF/WebP → fallback) or art direction. Always `width`/`height` (or `aspect-ratio`) to reserve space and prevent CLS. `alt=""` for decorative, meaningful `alt` otherwise.
- `color-scheme` meta/CSS + `prefers-color-scheme` for dark mode (both web and email — see §5).

## 5. HTML email — different rules

- **Table-based layout is still mandatory.** Outlook desktop (2016/2019/2021) uses Word's engine: no flexbox, no grid, no most modern CSS. Use `<table role="presentation">` for structure so screen readers skip the layout grid.
- Semantic content tags (`<h1>`–`<h3>`, `<p>`) DO work everywhere and help screen-reader scanning — use them inside the table cells.
- `alt` on every image (images often blocked by default); `lang` on `<html>`; logical heading order; AA contrast.
- Inline CSS (many clients strip `<style>`); web-safe/system fonts (Arial, Georgia, Helvetica) — custom web fonts unreliable.
- Dark mode: add `color-scheme`/`supported-color-schemes` meta; expect Apple Mail to aggressively invert, Outlook desktop to barely change, Gmail to partially invert. Design logos/buttons to survive inversion. Do NOT rely on `loading`/`fetchpriority`/`<dialog>`/popover/JS in email.
- Accessibility is now a **deliverability signal** (Gmail/Yahoo/Apple 2026) — alt text, contrast, semantics affect inbox placement.

## 6. Pitfalls / anti-patterns

- **Div soup**: nesting `<div>`s where a landmark/list/heading belongs. Reach for the semantic element first.
- Click handlers on non-interactive elements (`<div>`/`<span>`/`<li>` onclick) — inaccessible; use `<a>`/`<button>`.
- Placeholder as label — placeholders vanish on input, fail contrast, aren't announced reliably. Always a real `<label>`.
- Heading-level skips (h1→h4) or using headings for visual size. Style with CSS.
- `outline:none` on focus with no replacement — strands keyboard users.
- ARIA misuse: redundant roles (`<button role="button">`), `role` on the wrong element, `aria-label` on non-interactive/generic elements, hiding focusable content with `aria-hidden="true"`.
- Empty/missing `alt`, or alt that says "image of".
- Positive `tabindex` values (reorders tab flow unpredictably).
- Injecting into an `aria-live` region that didn't exist at page load (updates may not announce).
- Multiple `<h1>`/`<main>`, or landmarks with no distinguishing label when duplicated.

---

Sources (verify current): [WCAG 2.2 W3C](https://www.w3.org/TR/WCAG22/), [What's New in 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/), [Popover API — Baseline newly available (Jan 2025)](https://web.dev/blog/popover-baseline), [Customizable select — MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/appearance#base-select), [Invoker Commands Baseline — InfoQ](https://www.infoq.com/news/2026/01/html-invoker-commands/), [MDN fetchpriority](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/fetchpriority), [web.dev lazy loading](https://web.dev/articles/browser-level-image-lazy-loading), [Litmus email accessibility](https://www.litmus.com/blog/ultimate-guide-accessible-emails), [Email Markup Consortium 2026 report](https://emailmarkup.org/en/reports/accessibility/2026/).
