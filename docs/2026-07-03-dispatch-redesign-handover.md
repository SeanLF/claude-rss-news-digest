# Handover — "Dispatch" digest redesign → implementation

Paste this into a fresh session to continue. It's a planning/exploration handover, **not** an
instruction to start coding — Sean wants to explore the fixes + architecture implications, and
maybe mock the other circulation pages, *before* implementing.

## What happened (prior session)

We redesigned the news digest's visual language into a serif "wire briefing" (kept the name
**"Sean's Daily Digest"**, coloured the word "Digest" in accent). We built a full, real-content
mockup (the actual 17-story 2026-07-03 digest) and a canonical design-system spec, both mature and
reviewed. This was design work only — **no production code (`render.py` / `digest-template.html`
/ `digest.css` / circulation) was changed.** (Earlier in that session, separate already-shipped
work: dep bump, `/today/translate` + `?lang`, removed Yes/No feedback, feedback→mailto, emoji drop,
stats copy — all committed-pending on `main`; see the "Shipped" section of the memory file.)

## Read these first (in order)

1. **`docs/design-system.md`** — the canonical spec and the **port driver**. Tokens, 8-step type
   scale, letter-spacing tiers, 4px grid, every component (incl. the 3-tier sources + bias glyph),
   the **Circulation chrome** section (index/sources/stats/search/threads/feedback migration map),
   seanfloyd.dev lineage, email constraints, a11y checklist, CSS pitfalls.
2. **Memory `project_dispatch_redesign.md`** — full state, every decision + rationale, the artifact
   evolution log, and the **FINAL REVIEW punch-list** (fixes to apply), and the shipped/committed items.
3. **The final mockup** — published Artifact (stable URL, `WebFetch`-able):
   `https://claude.ai/code/artifact/7416748c-9553-4ecb-830a-8e2be9a7c286`. Source HTML at
   `/Users/sean/.claude/jobs/34bef7cd/tmp/dispatch_artifact.html` (prior job dir — may or may not
   persist; regenerable from `.../tmp/digest_data.json`, the parsed real digest). If neither
   persists, the doc fully specifies it.

## Status

- Mockup = **v22**, "pretty much solid" per Sean. Lighthouse a11y **100**.
- Final review done: **no Critical defects**; core verified clean. Remaining = a punch-list of
  Serious/Moderate/Minor polish + doc-sync (in the memory file). Highlights: `--bias-*` tokens
  missing from `data-theme` blocks + doc token table; numbered-link `aria-label`s; three stale doc
  lines (bias-bar position, share-anchor label, brief order); `.src-table` 12.5→12px; summary hit
  area <24px; single-bucket bias bar reads as skeleton; **"1 sources" plural bug**; dead `.srcs*` CSS.

## Next-session paths (Sean wants to explore, then decide — don't jump to implementation)

**Path A — Finalize the reference (fast).** Apply the review punch-list to the mockup + sync the
doc's stale lines. Leaves a clean, self-consistent spec. Low risk, ~1 pass.

**Path B — Design/architecture implications + port plan (explore first).** Produce a written plan, not code:
- **Past digests are frozen blobs.** Each digest is rendered once and stored as HTML with `digest.css`
  inlined, in the `digests` table; circulation serves the blob + a thin shell. So a template change
  affects **new digests only**; the archive keeps its old look. Decide: leave the archive as an
  honest design-evolution record (recommended), or backfill by re-rendering past digests (only where
  the source `selections` data survives). The circulation **shell** (nav wrap), however, applies to
  *all* served pages — keep shell changes backward-compatible.
- **Email vs web split.** The digest HTML is one artifact for both inbox and web. The new design uses
  flexbox (bias bar, masthead) and `<details>` (sources) — both need email-safe handling: table/`%`
  bias bar for Outlook (Word engine, no flex), `<details>` degrades to *visible* (Gmail→`<u>`,
  Outlook strips) so Tier-3 sources are `web-only` + email links out. The `.email-only`/`.web-only`
  class split (already in the codebase) is the mechanism.
- **Port scope:** rewrite `newsroom/templates/digest-template.html` + `render.py`'s per-article HTML
  + `newsroom/templates/digest.css`; self-host **Source Serif 4** woff2 (from
  `~/Developer/seanfloyd.dev/public/fonts/`) for circulation; extract the mockup's `<style>` into a
  real `digest.css` (clean: tokens + component classes; base64 font → self-hosted). Structure is
  *moderately* different (proper h1→h2→h3 tree, sources-glyph-summary+table, bias bar, deck,
  `<section aria-labelledby>`, `<time>`, eyebrow-below-headline) but the **content model + `selections.json`
  data are unchanged**. TDD per project rules; `make ci` gate; commit only when asked (branch off `main`).

**Path C — Mock the other circulation pages first (for Sean to validate before implementing).**
The design system covers only the *digest* so far. Before porting, mock the chrome pages
(index/sources/stats/search/threads/feedback) in the new system per the doc's **Circulation chrome**
section (token migration `--ruby→--accent-ink`/`--border→--hair`/etc., Source Serif 4, mono labels,
bias-spectrum, factuality badges as `--ok`/`--warn`, stats tables, forms, month accordions, thread
status). Deliver as an Artifact (or a few) for visual validation, same as the digest mockup was.
**Sean explicitly wants this option considered.** The two genuine product decisions there:
**status colours** (adopt `--ok`/`--warn` semantic axis, separate from the accent) and **border-radius**
(one `--radius:3px` on form controls only; flat elsewhere) — both proposed in the doc, confirm with Sean.

**Recommended sequencing:** A (finalize reference) → C (mock the other pages, validate) → B (port
plan) → implement. But let Sean choose — he may want B before C, or to implement the digest first.

## Locked decisions (don't relitigate; detail in doc + memory)

Name "Sean's Daily **Digest**". Serif-led (Source Serif 4). 8-step scale, 3 letter-spacing tiers,
4px grid. Reading column 680px in an 800px frame (~68 CPL, evidence-backed). Contrast #191917/#fafaf8
(16.6:1). bg cooled to #fafaf8. Sources = bias glyph (always visible, IS the `<summary>`) → spectrum
table on expand → email static glyph + "view online". Never drop article links (verifiability =
credibility). Safari Reader is structurally excluded (don't chase it; the digest *is* the reader view).
Region field dropped. seanfloyd.dev = canonical shared foundation (8 colour hexes + fonts + focus);
digest may diverge where it looks better (per Sean).

## Open questions for Sean

1. Sequencing: A→C→B→implement, or a different order? Digest-first or all-pages-mockup-first?
2. Backfill past digests to the new design, or leave the archive as-is?
3. Confirm the two circulation product decisions: status colours (`--ok`/`--warn`) and `--radius:3px` on forms only.
4. Which surface leads — is email or web the priority for the first implementation pass?
