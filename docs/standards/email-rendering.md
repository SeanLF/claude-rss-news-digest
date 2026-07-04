# HTML Email Rendering Standards (mid-2026)

> As of 2026-07: verify against [caniemail.com](https://www.caniemail.com) and current Litmus/Email on Acid data before relying on any claim here. Email support drifts fast — especially the Outlook engine migration (below).

Context for this repo: the daily digest ships as HTML email via Resend; shared CSS is resolved to **light mode** for the email surface, wrapped in an `email-only` class, with web/email surfaces sharing one template. Prioritize Apple Mail + Gmail; treat Outlook as the constraint that caps ambition.

## 1. Client landscape

- **Apple dominates opens: ~59–65%** (Litmus, opens-based). Apple Mail on iOS/iPadOS/macOS is the primary target. Note: opens over-count Apple — a Gmail account read in Apple Mail counts as an Apple open.
- **Gmail ~24–32%** (second). Two distinct engines: **webmail** (Chrome/desktop browser) and the **Gmail mobile app** (iOS/Android). They differ, especially on dark mode. Gmail strips `<head>` styles in some contexts historically — modern Gmail keeps a single `<style>` in `<head>` for webmail/app but **not** for non-Gmail accounts viewed in the Gmail app ("GANGA"), where `<style>` is dropped → media queries dead there.
- **Outlook ~4–6%**, but disproportionately painful. Mid-2026 is peak **dual-engine transition**:
  - **Classic Outlook (2016/2019/2021/desktop)** = Microsoft **Word rendering engine** (`mso`). No flexbox/grid, no `max-width`, no `background-image` (without VML), no `border-radius`, poor `padding` on non-table elements. **Microsoft has extended classic Outlook support to ~2029.** April 2026 is only the *opt-out* phase (new Outlook becomes the default, but users can still revert to classic); a later "cutover" (date unannounced) ends the ability to switch back. So the Word engine — and hand-installed/perpetual-license copies — persists for years: **keep tables**, the constraint holds longer, not shorter.
  - **New Outlook for Windows** + **Outlook.com** + **Outlook mobile** = Chromium/modern web engine (like Outlook.com). Supports flexbox, media queries, background images, `border-radius`, web fonts. Microsoft is mid-migration (business tenants auto-moved through 2025); you must support **both** simultaneously through and past 2026.
- **Yahoo/AOL ~2–3%**, Samsung Mail, Thunderbird (supports `prefers-color-scheme`) round out the tail.

## 2. Layout reality

- **Table-based layout is still required** as the baseline — solely because classic Word-engine Outlook has no flexbox/grid and ignores `max-width`. Everywhere else modern CSS layout works.
- **600px is the convention** for the fixed content column (safe max for desktop preview panes). Some go 640px.
- **Hybrid / "spongy" technique** is the robust responsive pattern: outer `<table width="100%">` constrained by a `<div style="max-width:600px">`, with a **`<!--[if mso]>` ghost table** at fixed 600px so Word-engine Outlook (which ignores `max-width`) still gets a bounded width. Advantage: works without needing media-query support.
- Media-query responsive works on Apple Mail, most webmail, and iOS/Android where `<style>` survives — but **not** GANGA (non-Gmail in Gmail app) and **not** classic Outlook. Hybrid degrades gracefully there; media queries are progressive enhancement.
- Modern CSS (flexbox/grid) is viable **only** if classic Outlook is a non-audience or you provide an MSO fallback. For a broad public digest, keep tables.

## 3. CSS support

- **Inline styles are the safe default** — always inline the critical presentational CSS (some clients strip `<style>`).
- **`<style>` in `<head>` survival:** yes on Apple Mail, Outlook.com/new Outlook, Gmail webmail + Gmail app for **Gmail-hosted** accounts; **stripped** for GANGA and unreliable elsewhere. So `<style>` = enhancement (media queries, `:hover`, dark-mode blocks), never load-bearing.
- **Media queries:** supported Apple Mail, iOS/Android Mail, Outlook app, Samsung, Thunderbird; **not** GANGA, **not** classic Outlook. Only one `<style>` block reliably respected by Gmail.
- **Breaks / avoid without fallback:** `flex`/`grid`, `position`, `float` (spotty in Outlook), `max-width` (ignored by classic Outlook), `background-image` (needs VML in Outlook), `border-radius` (square in classic Outlook), external web fonts (fall back to system stack; Outlook classic ignores), `margin` on some elements (prefer table cell `padding`), CSS `calc()`, custom properties (`var()` unreliable — do not depend on it for email; resolve to concrete values).
- **MSO conditional comments:** `<!--[if mso]>…<![endif]-->` targets Word-engine Outlook only; `<!--[if !mso]><!-->…<!--<![endif]-->` for everyone-but-classic-Outlook. Use for ghost tables, `mso-` props (`mso-line-height-rule:exactly`, `mso-padding-alt`), and **VML** for background images / bulletproof buttons in Outlook.

## 4. Dark mode

- Three client behaviours: **(a) respects `prefers-color-scheme`** — Apple Mail (iOS/macOS), Outlook 2019+/Apple-hosted, Samsung, Thunderbird: you control the palette. **(b) forced full inversion, limited override** — Gmail app (iOS inverts everything; Android uses brightness threshold then CIELAB inversion). **(c) partial/contrast-based inversion** — Outlook.com/webmail checks contrast ratios; Apple Mail uses HSL inversion on light-ish colors.
- **Meta tags (add both):** `<meta name="color-scheme" content="light dark">` and `<meta name="supported-color-schemes" content="light dark">`. Only **Apple Mail** truly honours them; Gmail/Outlook ignore and apply their own transforms — but they signal intent and reduce over-eager inversion.
- **`@media (prefers-color-scheme: dark)`** is the only mechanism giving real control, and only on group (a). This repo currently resolves email to **light mode** — that's a defensible choice, but expect Gmail/Outlook to auto-invert it anyway.
- **Contrast survival matters more than a custom dark palette:** clients invert *pure* colors but often leave near-white/near-black partially inverted, producing muddy low-contrast results. Avoid pure `#fff` backgrounds with mid-grey text; avoid transparent PNG logos that vanish on dark (use a bg-plate or a `<picture>` swap where supported). Test the light-mode email under forced inversion, not just in light.

## 5. Best practices

- **Authoring approach — hand-authored, no build.** The mature email frameworks are worth knowing: **MJML** (semantic components → bulletproof table HTML, MSO ghost tables handled for you), **Maizzle** (Tailwind-based, build-time inline + minify), **React Email** (JSX components, good for JS/React shops). We hand-author instead because this repo shares **one template between web and email**, wants **full control** over the exact MSO/VML output, and keeps a **no-build** pipeline — the frameworks buy convenience at the cost of a toolchain and a layer between you and the quirks. Reach for MJML/Maizzle only if template complexity outgrows hand-authoring.
- **Inline all critical CSS** (a build-time inliner, e.g. juice-style); keep one `<style>` block for hover/media/dark enhancements.
- **Semantic + `role="presentation"`** on every layout `<table>` so screen readers skip them; use real `<h1>`/`<p>`, meaningful reading order, `lang` attribute.
- **Alt text on every image**; assume images are blocked by default (Outlook, some Gmail). Styled alt text (color/size on `<img>`) helps blocked state.
- **Bulletproof buttons:** padded `<a>` styled as a block on a table cell, with an MSO/VML fallback so classic Outlook renders a real clickable shape — never rely on a background-image button.
- **Preheader:** a hidden ~40–100 char snippet at the top (`display:none;max-height:0;overflow:hidden`) that becomes the inbox preview — treat as a second subject line. This repo already replaced `regional_summary` with a preheader.
- **Gmail 102KB clipping:** Gmail truncates HTML past ~102KB → "Message clipped", hiding footer/unsubscribe/CTA. Minify HTML, inline only needed CSS, host images externally (don't embed base64), keep the DOM lean.
- **Plain-text multipart part:** always send a `text/plain` alternative alongside `text/html` — improves deliverability/spam score and serves text-only clients. Confirm Resend is sending both.
- **One-click unsubscribe (RFC 8058):** bulk senders (5k+/day to a provider) MUST send a DKIM-signed `List-Unsubscribe` header **plus** `List-Unsubscribe-Post: List-Unsubscribe=One-Click` so the mailbox UI's unsubscribe button does an HTTPS POST; honour it within 48h. Mandatory since Feb 2024 (Gmail/Yahoo) — **Gmail now permanently rejects** non-compliant bulk mail (Nov 2025), Microsoft enforces too (May 2025). **Resend Broadcasts** (what this repo uses) inject both headers automatically at the Audience level; **raw `Emails.send` does NOT** — any hand-sent bulk must add them itself. This sits on top of authentication: **SPF + DKIM + DMARC at enforcement** (not `p=none`) and keep the spam-complaint rate **< 0.3%**.
- **BIMI + VMC/CMC (brand logo in the inbox):** publish a BIMI DNS record pointing at a square SVG Tiny-PS logo to show your brand mark beside the sender name. Requires **DMARC at `p=quarantine` or `p=reject`**; a verified mark certificate — VMC (trademark-based) or the cheaper **CMC** (Gmail lowered the barrier by accepting CMCs in early 2025) — is needed for Gmail/Apple. Deliverability + trust signal, not just cosmetics.
- **Images:** absolute HTTPS URLs, explicit `width`/`height`, `max-width:100%` for fluid, retina via 2× served-down. Total email weight modest; lazy blocking is the norm.
- **Accessibility:** min ~14–16px body, ≥4.5:1 contrast, don't convey meaning by colour alone, tap targets ≥44px.

## 6. Testing & pitfalls

- **Render-test across real clients**, not just a browser: Litmus or Email on Acid (paid, screenshots across dozens of clients incl. classic + new Outlook, Gmail app vs webmail, dark mode). Free-ish: Testi@/Mailtrap, `darkmodechecker.org` for dark previews. **Send a real test to your own Gmail + Apple Mail + an Outlook account** before every meaningful template change.
- **Common pitfalls:**
  - Assuming `max-width` bounds width in classic Outlook (it doesn't — add the MSO ghost table).
  - Trusting `<style>`/media queries in the Gmail app for non-Gmail accounts (stripped).
  - Depending on `var()`/`calc()`/flexbox without an inlined concrete fallback.
  - Transparent-PNG logos disappearing under dark-mode inversion.
  - Going over 102KB and getting the footer clipped in Gmail.
  - Extra whitespace/line-height in Outlook (set `mso-line-height-rule:exactly`, zero out image gaps with `display:block`).
  - Forgetting the plain-text part (spam-score hit).
  - Testing only in light mode — clients auto-invert, so verify the inverted result.

## Sources (verify current)

- [Litmus — Email client market share](https://www.litmus.com/email-client-market-share)
- [Email on Acid — New Outlook for Windows](https://www.emailonacid.com/blog/article/industry-news/new-outlook-for-windows/)
- [Litmus — Outlook rendering differences](https://www.litmus.com/blog/a-guide-to-rendering-differences-in-microsoft-outlook-clients)
- [Can I email — prefers-color-scheme](https://www.caniemail.com/features/css-at-media-prefers-color-scheme/)
- [Litmus — Ultimate guide to dark mode](https://www.litmus.com/blog/the-ultimate-guide-to-dark-mode-for-email-marketers)
- [Email on Acid — Dark mode for email](https://www.emailonacid.com/blog/article/email-development/dark-mode-for-email/)
- [MailPeek — Client rendering differences 2026](https://dev.to/mailpeek/the-complete-guide-to-email-client-rendering-differences-in-2026-243f)
- [Microsoft extends classic Outlook support to 2029](https://www.techrepublic.com/article/news-microsoft-extends-classic-outlook-retirement-deadline/)
- [Resend — Gmail/Yahoo bulk sending requirements (RFC 8058)](https://resend.com/blog/gmail-and-yahoo-bulk-sending-requirements-for-2024)
- [RFC 8058 — one-click List-Unsubscribe](https://www.rfc-editor.org/rfc/rfc8058.html)
