# JavaScript standards reference (browser, vanilla)

> As of 2026-07. Fast-moving surface — verify any version/baseline claim against caniuse / web.dev Baseline / MDN before relying on it in shipped code.

Scope: this repo is **vanilla JS only** — no framework, no bundler, no build step. Target is small progressive-enhancement scripts (e.g. a ~30-line "load more" over HTML fragments with a real `<a>` fallback). Keep it that way. If a change seems to want a framework or build step, that's a signal to reconsider the change, not the constraint.

## 1. Language + platform baseline (mid-2026)

- **ES modules run natively** in every current browser — `<script type="module">`, static `import`/`export`, dynamic `import()`. No bundler needed. Modules are deferred and strict-mode by default, and have their own scope (no global leak). Prefer them over IIFEs.
- **Top-level `await`** works in modules — fine for one-off init, but it blocks the module graph, so don't gate first paint on it.
- `fetch`, `AbortController`/`AbortSignal`, `Promise`, `async`/`await` are all long-since Baseline Widely available. Use `fetch` + `AbortController` (with `AbortSignal.timeout(ms)`) rather than `XMLHttpRequest`.
- `structuredClone(obj)` — native deep clone, Baseline Widely available. Use instead of `JSON.parse(JSON.stringify(...))`.
- **ES2024/2025 worth reaching for** (all shipped in current browsers; still verify if you must support older): `Array.prototype.at()`, `Object.groupBy()` / `Map.groupBy()`, `Array.fromAsync()`, **Iterator helpers** (`.map`/`.filter`/`.take`/`.drop` lazily on any iterator), **Set methods** (`.union`/`.intersection`/`.difference`), `Promise.try()`, `Promise.withResolvers()`, `RegExp.escape()` (safe interpolation into regex), `String.prototype.replaceAll`, top-level `?.`/`??`. ES2025 was ratified June 2025.
- **JSON module imports** (`import data from "./x.json" with { type: "json" }`) standardized in ES2025 — but browser support is still uneven; prefer `fetch` for JSON at runtime unless you've confirmed support.
- **Temporal**: reached Stage 4 (ECMAScript 2026); shipping in Firefox (139+, 2025) and Chrome (144, early 2026), Safari still partial/flagged. NOT yet cross-browser safe — do **not** use `Temporal` in shipped browser code without a polyfill or a confirmed support check. `Intl.DateTimeFormat` covers most display needs today.
- Modern DOM you should default to: `element.closest()`, `matches()`, `el.toggleAttribute()`, `classList`, `dataset`, `querySelector(All)`, `AbortController` to remove listeners (`addEventListener(t, fn, { signal })`), `IntersectionObserver` (viewport/lazy triggers), `ResizeObserver`, `CustomEvent`, `el.replaceChildren()`, `insertAdjacentHTML`.
- Prefer `Element.setHTMLUnsafe()` / sanitizer API only where you've checked support; otherwise treat any HTML-string insertion as a trust decision (see pitfalls).

## 2. Philosophy / idioms for no-build vanilla

- **Progressive enhancement is the house style.** The page must work with zero JS: real `<a href>` / `<form action>` that hit the server and return HTML. JS intercepts and upgrades the same action to be smoother — it never *provides* the only path to content.
- **HTML-over-the-wire, not SPA.** Server renders HTML fragments; JS swaps them into the DOM. No client-side router, no virtual DOM, no client state store. The 2025–26 industry drift is explicitly back toward this ("do we need a framework here at all?").
- **Unobtrusive JS**: behaviour lives in external `.mjs`/`.js` modules, not inline `onclick`. Hook by `data-*` attributes or semantic selectors, not by presentation classes.
- **Event delegation**: bind one listener on a stable container and match with `e.target.closest('[data-action]')`, rather than binding N listeners to N elements — survives DOM swaps and is cheaper.
- **Native Web Components** (Custom Elements + Shadow DOM + `<template>`) are Baseline and buildless-friendly for genuinely reusable, encapsulated widgets. Overkill for a 30-line enhancement — reach for them only when you have real reuse/encapsulation needs.
- **Same-document View Transitions from JS** — `document.startViewTransition(() => { /* swap DOM */ })` (Baseline; Firefox 144 completed the set, 2025). The buildless upgrade for this repo's HTML-over-the-wire fragment swaps: wrap the `replaceChildren`/`insertAdjacentHTML` in it for a smooth crossfade, with automatic no-op fallback where unsupported (feature-detect `if (document.startViewTransition)`). Gate behind `prefers-reduced-motion`.
- **Speculation Rules API** (`<script type="speculationrules">` for `prerender`/`prefetch`) — instant navigation for the server-rendered archive / month pagination. Chrome/Edge shipped; Safari 26.2 behind a flag; **not Firefox** (mid-2026). Pure progressive enhancement — ignored where unsupported, so the plain links still work. Prefer conservative `eagerness` and same-origin to avoid over-prerendering.
- **Import maps** (`<script type="importmap">`, Baseline 2023) let you use bare-specifier ESM (`import x from "lib"`) with no build step — the buildless way to pin/alias module URLs. If you ever *must* bundle, esbuild/Bun are SOTA — but you don't here.

## 3. Best practices to default to

- **Feature-detect, then degrade.** `if ('IntersectionObserver' in window) {…}` — if the API is missing, the no-JS fallback (the plain link) is already there. Never assume.
- **Keep JS optional and additive.** Enhance in a way that, if the script throws or never loads, the user still completes the task via the underlying HTML.
- **Accessibility of dynamic updates**: when JS injects/replaces content, announce it (`aria-live="polite"` region for "N more loaded", or `role="status"`), and **manage focus** — move focus to the new content's heading or the next actionable element so keyboard/AT users aren't stranded. Don't let a swap silently change the page under a screen reader.
- **No global scope leaks**: use modules (auto-scoped) or an IIFE if you must use a classic script. Never hang state off `window`.
- **Small, dependency-free modules**: one file, one concern, a named export or two. No npm, no polyfill-by-default. Every dependency is liability here.
- **Idempotent init**: guard against double-binding (e.g. after a fragment swap re-runs setup) — mark handled nodes (`dataset.enhanced`) or rely on delegation from a container that isn't itself replaced.
- **Respect user prefs**: check `matchMedia('(prefers-reduced-motion: reduce)')` before animating scroll/transitions.

## 4. What changed in the last ~2 years

- **The buildless / "you might not need a framework" shift is now mainstream**, not contrarian. Native ESM + HTTP/2+ removed the original reason bundlers existed for small sites. "Write files, open in a browser, deploy anywhere" is a respected default for this class of work.
- Baseline (web.dev) gave a shared vocabulary — "Widely available" (≈30 months interop) vs "Newly available" — so "can I use this?" is answerable without guessing browser matrices.
- Iterator helpers + Set methods (ES2025) removed a big chunk of what people used lodash for.
- `Promise.withResolvers()` and `Promise.try()` cleaned up common async plumbing.
- Temporal finally landed in the spec (ES2026) and is mid-rollout across browsers — the long-awaited `Date` replacement, but not yet universally shippable.
- Popover API, `dialog`, `:has()`, container queries etc. moved a lot of former-JS behaviour into HTML/CSS — check whether a thing even needs JS first.

## 5. Pitfalls / anti-patterns to avoid

- **JS-required content**: rendering primary content only via JS (blank page without it). Breaks no-JS, slow networks, and error cases. Content comes from the server; JS enhances.
- **Layout jank / CLS**: injecting content that shifts the page. Reserve space, append below the fold, or keep the trigger's position stable. Move focus deliberately, don't let the browser jump.
- **Unhandled promise rejections**: every `fetch`/`await` path needs a `.catch` or `try/catch`. On failure, fall back to (or leave intact) the plain-link behaviour and surface a visible, accessible error — never fail silently.
- **Aborted-request noise**: an `AbortError` from `AbortController` on teardown is expected — filter it (`if (e.name !== 'AbortError')`) so it doesn't log as a real error.
- **Memory leaks from listeners**: listeners on nodes you later remove/replace, or on `window`/`document`, that are never cleaned up. Use `{ signal }` to auto-remove, or delegate from a persistent container. `IntersectionObserver`/`ResizeObserver` must be `.disconnect()`ed when done.
- **XSS via HTML strings**: `innerHTML`/`insertAdjacentHTML` with server HTML is fine only because the server controls it — never interpolate user/URL data into an HTML string. Use `textContent` for text, or build nodes.
- **Double-binding after DOM swaps**: re-running init over already-enhanced nodes → duplicate handlers, duplicate fetches. Guard init.
- **Assuming brand-new APIs everywhere**: don't ship `Temporal`, JSON import attributes, or other freshly-landed features without a support check or a fallback.

## Sources (verify against these, mid-2026)

- Baseline / feature availability: <https://web.dev/baseline> · <https://developer.mozilla.org/en-US/docs/Glossary/Baseline/Compatibility> · <https://caniuse.com>
- ES2025 features: <https://www.infoworld.com/article/4021944/ecmascript-2025-the-best-new-features-in-javascript.html> · <https://tc39.es/ecma262/2025/>
- Temporal status: <https://github.com/tc39/proposal-temporal> · <https://socket.dev/blog/temporal-api-ships-in-chrome-144-major-shift-for-javascript-date-handling>
- Buildless / no-framework shift: <https://thenewstack.io/why-developers-are-ditching-frameworks-for-vanilla-javascript/> · <https://mxb.dev/blog/buildless/> · <https://medium.com/@Nexumo_/progressive-enhancement-in-2025-actually-works-70213ab06777>
