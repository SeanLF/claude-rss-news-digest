# MJML email migration — design

**Status:** planned (PoC done, greenlit 2026-07-05)
**Problem:** one hand-authored template + CSS serves BOTH the HTML email and the
persistent web archive. Email-safety (table layouts, `&nbsp;` glue, `!important`,
MSO wrapper, Outlook.com quirks) has degraded the shared source and coupled the two
tightly. The web is the canonical/complete/persistent version and should be clean.

## Decision

Treat **`selections.json` as the canonical model** and render it through **two
presentation paths** — there is no tool that turns clean web HTML into robust email
(flex/grid vs email-tables are different layout models; MJML/Maizzle/react-email are
all email-first authoring). So:

1. **Web** — a clean, semantic HTML/CSS template (current `digest-template.html`,
   with the email hacks reverted), served by circulation. Modern CSS, no email
   constraints. This is the reference design + archive.
2. **Email** — a Jinja template that emits **MJML**, compiled to Outlook-hardened
   HTML by **`mjml-python`** (the `mrml` Rust engine — native Python, **no Node**).

### PoC evidence (`scratch/mjml-poc/`)
- MJML reproduces the design pixel-identically (`mrml_poc.png`).
- Auto-generates the Outlook plumbing: ~53 MSO conditionals + 14 ghost tables —
  exactly the hand-fought fixes (section rule, article/footer separators, bias bar).
- `mjml-python` (mrml) compiled the PoC natively in the venv; feature parity with the
  Node engine confirmed for this design (bias-bar `mj-table`, `mj-divider`, borders).

## Plan

**Phase 1 — MJML email renderer (the hard 80%).**
- Add `mjml-python` to newsroom deps (pyproject + Dockerfile; pure-wheel, no Node).
- `newsroom/templates/email.mjml.j2` — Jinja→MJML for the full digest: top utility
  line, masthead, section headers, story (headline/lede/why/varies/thread-eyebrow/
  bias-bar/source-line), brief, single/no-source, footer, preheader.
- `render_email(selections, ...) -> str` — fill the Jinja MJML with data + resolved
  tokens/urls/issue-no, `mjml2html()` it, return email HTML. Replaces
  `prepare_for_email` for the send path.
- Wire the send path (`broadcast`/`--test-send`) to `render_email`.

**Phase 2 — clean the web template.**
- Revert the email hacks in `digest-template.html`/`digest.css`: section header back
  to flex, separators back to `border`/`margin`, drop MSO wrapper / `&nbsp;` glue /
  `!important` / the email-only surfaces (view-in-browser, unsubscribe, preheader)
  that now live only in the MJML email.
- `prepare_for_web` becomes a no-op (or is removed); circulation still injects chrome.

**Phase 3 — tests + verification.**
- Replace `test_pipeline_contract` (the shared-template flip contract is gone).
- New: email renders via mrml without error + covers all states; web renders clean.
- Verify kitchen-sink through both; send test emails; Safari the web view.

## Risks / notes
- **Two templates, one design** — maintained twice (web CSS + MJML). Mitigate: share
  tokens/copy/ordering via the data model; design is stable post-redesign.
- **mrml feature coverage** — spiked OK for this design; if a component is missing,
  fall back to the pure-Python MJML port or a Node MJML sidecar (last resort).
- **Jinja→MJML→HTML** is two template layers; keep the Jinja thin (loops/conditionals
  emitting MJML blocks) so it stays debuggable.
- The current hand-rolled email works across Gmail/Outlook/Safari and ships until this
  lands; the migration is not urgent, just correct.
