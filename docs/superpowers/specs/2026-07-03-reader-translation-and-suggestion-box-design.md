# Reader Translation + Suggestion Box — Design

**Date:** 2026-07-03
**Status:** Proposed (spec review)
**Surfaces:** `circulation/` (web) + digest email
**Guiding constraint:** zero new infra, zero cookies, zero storage, zero per-reader tracking. Every part leans on infrastructure that already exists (Google's translation proxy, the reader's mail client, existing request logs).

---

## 1. Overview

Two small, related reader-facing features:

- **A. Translate** — let a reader view any digest in their own language, on demand, via Google Translate's page proxy. One control on the web page, one link in the email, both routed through a single server redirect.
- **B. Suggestion box** — a lightweight qualitative feedback channel: reply to the email, or a `mailto:` link on the web. No form, no database.

Both are deliberately minimal. The digest is a curated, privacy-respecting editorial product at personal scale; the design matches that — qualitative over quantitative, opt-in over inferred, hand-off over hoard.

### Goals
- A non-native English reader can read the digest in their language with one click, layout intact.
- A reader can send feedback in their own words with minimal friction.
- No tracking, no cookies, no stored personal data, no ongoing cost.

### Non-goals (explicitly rejected — see §7)
- Server-side / DeepL translation and caching. **Will never invest in a paid translation upgrade.**
- A maintained in-app language menu (server auto-targets a non-EN language; Google's on-page picker backstops the tail).
- A detected-language banner (guessing in the render path; redundant with native browser translation).
- Quantitative analytics / per-article click tracking.

---

## 2. Background & key findings

Established during brainstorming (2026-07-03), several empirically verified:

- **Google's `translate.goog` proxy works and preserves layout.** Verified live: the French render kept headlines, `why_it_matters`, tier pills, terracotta accent, medals. Translation quality is genuinely good. Google's proxy page carries its **own language switcher** (`English → French ▾`) covering ~100+ languages — so we do not need to build or maintain a language menu.
- **Translation is client-side JS.** `translate.goog` ships the English HTML + Google's `translate_http` bundle; the browser does the word-swapping. Consequence: needs JS enabled (native browser translation covers the JS-off case on-site); and there is nothing to server-side fetch or cache — reinforces "link, don't store."
- **`tl=en` fails — do not ever emit it.** Verified live: a proxy URL with target = source = English returns Google's **"Can't translate this page"** error, and that error page has **no language picker** (only "Go to original page"). So English can never be a target, and you cannot reach Google's picker from an English no-op.
- **The `translate.google.com/translate?u=…` front-door is unusable and is NOT used.** For an English-first browser it resolves the target to `en` → the "Can't translate this page" error above. So the front-door is dead for exactly the readers who'd hit it. We build the **direct `.goog` URL only**, always with an explicit non-English `tl` (verified to render for fr/es/de/ja/pt).
- **Consequence — Google's on-page picker is a *backstop*, not an entry point.** It exists only on a successfully translated (non-English) page. So the server must always target a non-English language; once the reader is on any translated page, that picker covers the ~100-language tail. English-first readers (a large share of the translation audience — non-natives on English devices) therefore land on `DEFAULT_LANG` and switch via the picker if they want another.
- **`sl=en` is correct as source.** Google collapses regional English (`en-CA`/`en-US`) to one source model; the subtag is echoed but semantically inert. Use `sl=en`.
- **DeepL is out.** Its free API tier appears to be a one-time ~1M-character lifetime credit (not recurring), and we will never pay for an upgrade. Google's proxy is the only sustainably free option (Google bears the cost). Quality is Google-tier, which is accepted.
- **The app stores no IP.** `circulation` uses `into_make_service()` (no `ConnectInfo`), no `X-Forwarded-For` parsing, no IP anywhere; `TraceLayer` logs method/path/status only. IP-free by construction. (Caveat: the edge TLS terminator likely has its own access log with IP — pre-existing, see §6.)
- **Sending address `daily-news-digest@seanfloyd.dev` receives mail** via the iCloud catch-all on `seanfloyd.dev`. So "just reply" works with no `Reply-To` change.

---

## 3. Feature A — Translate

### 3.1 The redirect endpoint (the one moving part)

Add a route:

```
GET /{date}/translate  →  302 to a Google Translate URL
```

**Why an endpoint rather than a bare static link to Google:**
1. **Usage visibility for free** — the hit lands in the existing `TraceLayer` log (path only, no IP), so "is this ever used?" is answerable by grepping logs. No counter, no table, no tracking.
2. **A smarter default than Google's** — the server can apply the "first non-`en`" rule (below) to rescue English-first-but-lists-another readers, which the raw front-door cannot.
3. **One place to change** — if the proxy format ever breaks, one handler changes, not every baked email link.

**Handler logic:**

```
1. Validate `date` (existing is_valid_date); 404 if invalid or no such digest.
2. Read the `Accept-Language` request header.
3. Pick target language LANG (must never be "en" — see §9):
     - Iterate tags in listed order (browsers emit in preference/q order).
     - Take the first tag whose base language (before "-") != "en", and pass it to `tl`
       VERBATIM — no stripping, no special cases. Google tolerates region subtags
       (treats fr-CA≈fr, pt-BR≈pt) and honors Chinese script variants (zh-CN Simplified /
       zh-TW Traditional) for free. The only exclusion is `en` itself (§9).
     - If none (English-only, or header absent): LANG = "fr" (the house default; any
       non-English value works — the on-page picker lets the reader switch from there).
4. Build the direct proxy URL (front-door is NOT used — see §2):
     target = https://{PROXY_HOST}/{date}?_x_tr_sl=en&_x_tr_tl={LANG}&_x_tr_hl={LANG}
5. Return 302 to `target`.
```

- `{PROXY_HOST}` is **derived from the configured digest domain**, not hardcoded: replace `-` → `--`, then `.` → `-`, then append `.translate.goog`. For `news-digest.seanfloyd.dev` this yields `news--digest-seanfloyd-dev.translate.goog` (verified to resolve and render). Deriving it keeps `DIGEST_DOMAIN` the single source of truth.
- No allowlist of languages: pass the subtag through; Google tolerates unknown targets, and its on-page picker is the safety net.
- Stateless. No cookie, no storage, no IP read (the app can't see IP anyway).

### 3.2 On-site control (placement 01 from the mockup)

A **文A "Translate"** pill in the top utility row of the digest page, beside the section links. Always present; English readers ignore it. Links to `/{date}/translate`.

- **Own icon, not Google's** — a `文A` glyph in the digest's terracotta, matching the product's identity. Rationale: (a) avoids implying a Google partnership/endorsement (their brand guidelines restrict logo use); (b) their multicolor mark clashes with the restrained palette; (c) keeps us engine-agnostic; (d) Google brands *itself* on the proxy page at point of use, so attribution is already handled.
- Optional small `via Google Translate` caption for honesty (sets the machine-translation expectation without borrowing the trademark).
- `文A` chosen over a globe (globe reads as "region"; `文A` reads as "language/translate").

### 3.3 Email link (placement 03)

The **same** `/{date}/translate` link, in two low-key spots: a compact `文A Translate` next to "View in browser" at the top, and a plain-language line in the footer (*"Read this digest in another language →"*). The href is baked at send time, but **the redirect resolves at click time**, so each recipient still lands in their own language.

### 3.4 What we are NOT building
- **No language menu** — the server auto-targets (first non-`en`, else `DEFAULT_LANG`) and Google's on-page picker backstops the ~100-language tail once the reader is on a translated page. No curated list to maintain.
- **No detected-language banner** — it forced the guess into the render path and duplicated native browser translation. Dropped.
- **No caching / stored translations** — client-side JS translation makes it impossible to cheaply capture anyway, and we'll never pay for a quality tier.

### 3.5 Native browser translation (complement, no work)
On-site, Chrome/Safari/Edge already offer to translate the English page (via the correct `<html lang>`). This covers JS-off readers and browsers that auto-offer. We rely on it as a free complement; the 文A control serves email, English-first readers, and anyone who wants an explicit choice.

---

## 4. Feature B — Suggestion box

Qualitative, opt-in, zero-infra. Chosen over per-article vote analytics because at personal scale the *why* (one reader's sentence) beats the *count* (noisy thumbs), and a non-click on a summary product is an ambiguous-to-inverted signal.

- **Email:** footer line — *"Got feedback or a suggestion? Just hit reply."* Replies land in the iCloud catch-all at `daily-news-digest@seanfloyd.dev` (confirmed). No link, no `Reply-To` change; the reply thread carries digest context automatically.
- **Web:** a footer `mailto:` to the **same sending address**, with the date prefilled for context and no tracking:
  `mailto:daily-news-digest@seanfloyd.dev?subject=Digest%20feedback%20%E2%80%94%20{date}`
  Show the plain address as text beside the link for readers with no `mailto:` handler.

Both surfaces converge on one inbox. No storage, no server, no tracking.

### Open follow-up (not part of this spec)
The per-story up/down feedback (`render_feedback_thanks`, shipped 2026-07-02) is a thin signal. **Keep it for now**; let the suggestion box run alongside and decide keep-vs-remove with evidence of which gets used. Do not rip out just-shipped work reflexively.

---

## 5. Icon / branding decision
Use the **own `文A` mark** in the product's terracotta, not Google's logo (see §3.2 rationale). Optional `via Google Translate` text caption for expectation-setting. Google self-brands on the destination page.

---

## 6. Privacy

- **Required addition** — one sentence disclosing the Google hand-off, because clicking sends the reader's request (IP, page, language) to Google under Google's privacy policy:
  > *"If you choose to view a translated version of a digest, your request is sent to Google Translate and processed under Google's privacy policy. We don't control or receive that data."*
- **No cookies, no storage, no IP** in anything this feature adds. The suggestion box adds nothing (mail client + existing inbox).
- **Verify pre-existing coverage** — the app is IP-free, but the edge TLS terminator (on the Hetzner box) likely logs client IP in standard access logs. Confirm the current `/privacy` already covers server/security access logs; if not, that is a pre-existing gap to fix independently of this feature.
- **No new analytics.** "Is translate used?" is answered by existing `TraceLayer` path logs (no IP), not a new counter.

---

## 7. Decisions & rationale (the forks we resolved)

| Decision | Chosen | Why |
|---|---|---|
| Translation engine | Google `.goog` proxy | Only sustainably free option (Google bears cost); layout preserved; verified quality acceptable |
| DeepL | Rejected | Free tier looks like one-time lifetime credit; will never pay to upgrade |
| Server-side caching | None | Client-side JS translation → nothing to capture; no paid tier to cache for |
| Language selection | Server "first non-`en`", else `DEFAULT_LANG` (a non-EN default) | Front-door + `tl=en` both hit Google's "Can't translate" error; must always target a non-EN language. On-page picker backstops the tail |
| Language menu | None (use Google's on-page picker) | ~100+ langs, always current, zero maintenance |
| Detected-language banner | Dropped | Forces guess into render path; duplicates native browser translation; takes space |
| Icon | Own `文A` mark | Avoids trademark/endorsement issues; fits palette; engine-agnostic |
| Source lang | `sl=en` | Google collapses regional English; subtag inert |
| Feedback | Reply-to-email + web `mailto:` | Qualitative > quantitative at small N; zero infra; on-brand |
| Feedback analytics | None (existing logs only) | Won't act on a usage number; counting is theatre here |

---

## 8. Files likely touched (implementation follows existing patterns)

**circulation (Rust):**
- `src/main.rs` — register `/{date}/translate` route (routes module + Router).
- `src/handlers.rs` — new `translate_redirect` handler (Accept-Language parse, proxy-host derivation, 302); privacy-page sentence.
- `src/templates/digest.rs` — inject the `文A` control into the web utility row; add the web `mailto:` footer line. Follow the existing base-template vs `inject()` split in `handlers.rs`.

**newsroom (email/base template):**
- `templates/digest-template.html` — email `文A Translate` link (top) + "another language" footer line + "just hit reply" feedback line.

**Render-once-per-surface guard (resolve during planning):** the web page serves the *base* HTML plus web-only injected nav (`handlers.rs::inject`). So an element placed in the base template appears in **both** the email and the web page; an injected element appears **only** on web. To avoid a duplicated control on web, each element must render exactly once per surface:
- Elements that must appear in the **email** (translate link, reply line) go in the base template — and therefore also show on web, which is fine as long as they are **not** additionally injected.
- Where web placement should differ from email (e.g., the `文A` pill in the web utility row vs. the email header), pick one: a single shared base-template placement acceptable on both, **or** base-template for email + web-only injection with the base copy suppressed on web. Confirm the final DOM has one translate control and one feedback line per surface.

**Config:** none required (proxy host derived from existing `DIGEST_DOMAIN`).

---

## 9. Edge cases & gotchas

- **`translate.goog` is undocumented** — Google can change the `_x_tr_*` format without notice. Mitigation: it's just a link behind one handler; if it breaks, one change fixes it. Low risk, no infra damage.
- **Domain double-hyphen transform** is the one silently-breakable bit — `news-digest` → `news--digest`. Derive it algorithmically and **verify the derived host resolves + renders in a real browser** before shipping.
- **Google anti-abuse throttle** — rapid repeat hits from one IP hit `/sorry`. Individual readers never trigger it; do not machine-loop any Google Translate URL.
- **Client-side JS requirement** — proxy translation needs JS. Native browser translation covers the on-site JS-off case; nothing to do.
- **Never emit `tl=en`** (and never use the `translate.google.com/translate?u=` front-door) — both produce Google's "Can't translate this page" error, which has no picker. Always target a non-English language.
- **Region subtags** — pass the tag through verbatim; Google tolerates them (`fr-CA`≈`fr`, `pt-BR`≈`pt`) and honors Chinese script variants (`zh-CN`/`zh-TW`) for free. No stripping, no special-case. (An unsupported *language* still dog-errors — inherent to Google; acceptable, add an allowlist only if it ever bites.)
- **`mailto:` handler absence** — show the plain address as text fallback.

---

## 10. Testing / verification

- **Endpoint unit tests** (TDD): `Accept-Language` parsing — `fr-FR,fr;q=0.9,en;q=0.8` → `fr-FR`; `en-US,en;q=0.9,fr;q=0.8` → `fr` (first non-en, not the top tag); `en-US,en` → `DEFAULT_LANG` (`fr`); missing header → `DEFAULT_LANG`; verbatim pass-through `pt-BR` → `pt-BR`, `zh-TW` → `zh-TW`; and assert `tl` is **never** `en` (base-language `en` excluded). Proxy-host derivation from a sample domain.
- **Live regional check** — since curl can't run the client-side JS, eyeball a *regional* target in a real browser (e.g. `_x_tr_tl=fr-CA`) to confirm pass-through renders and doesn't dog-error.
- **Live render check** — open the derived `.goog` URL in a real browser (curl can't, since translation is client-side JS) and confirm the digest renders translated with layout intact and the on-page picker present.
- **Email check** — send a test digest; confirm the `文A` link resolves per the recipient's browser at click time, and that a reply lands in the catch-all inbox.
- **Regression** — existing digest page, feed, feedback vote, and OG injection unaffected.

---

## 11. Rollback
All-or-nothing per surface, but low-risk: the route and template additions are self-contained. Removing the route + template lines reverts cleanly. No migrations, no data, no config.
