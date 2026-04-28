# Design: Multi-Tenant, Multi-Vertical Digest Platform

**Date:** 2026-04-14
**Status:** Draft

## Summary

Generalise the news-digest pipeline from a single editorial output into a platform capable of running multiple content verticals for multiple tenants, with per-vertical configuration, isolated data, budget controls, and customisable schedules. Scope is capped at pilot readiness: onboard 2-3 paying pilot customers manually without building self-serve signup, billing automation, or public marketplace UI. Architecture must not foreclose those future layers.

## Motivation

Three concurrent drivers:

1. **Pilot candidates in hand.** Danny Dib (EU regulation), Hazem Hamrouni (B2B sales / EMEA startup ecosystem), and Gabriel Prá (LatAm LogTech / supply chain) have each expressed *interest* in a vertical-specific digest. The competitive research in §Competitive Landscape turns this into three separate questions: Danny is the only one whose vertical supports real pricing (€75-299/mo); Hazem caps at €39/mo; Gabriel's market has Bloomberg Línea at €8/mo and doesn't support paid pricing at all. "Willingness to pay" is only real at the price the vertical's economics require — anything less is polite interest.

2. **Architecture already close.** Existing codebase is more content-agnostic than it looks — the blocker is a small set of hardcoded taxonomy in `mcp_server.py`, `render.py`, and subagent prompts. Research in `docs/research/architecture-generalisation.md` maps the exact 5-step change set.

3. **Code-health case stands alone.** `docs/research/cost-model.md` + `docs/research/vertical-pricing-model.md` give honest numbers: €43-53/mo COGS per daily vertical on Sonnet-everywhere, €14 weekly. Pilot friend-pricing (€25-50/mo solo) sits at or below COGS once operator time is counted — the research is explicit that favourable shared-digest economics only kick in at 10+ subs. Firm-tier at €300-400/mo is viable for small law boutiques but speculative.

The generalisation work is justified by code-health and future experimentation headroom, not pilot revenue. Pilot revenue is signal (are these people actually reading it?) not ROI. That framing has to hold through the whole spec — if the refactor doesn't pay for itself in code quality, nothing in the pilot math rescues it.

## Goals

- Multi-tenant, multi-vertical: one operator can run N verticals for M tenants on one Hetzner host.
- Per-tenant data isolation at the filesystem level (not just `WHERE tenant_id = ?`).
- Per-vertical budget caps and schedules, configurable without code changes.
- White-label ready: from-name, from-domain, branding fields live in vertical config, not in code.
- Zero regression for the existing `news` vertical throughout migration.

## Non-Goals

- Self-serve signup flow, OAuth, password reset, email verification.
- Stripe subscriptions or any automated billing. Pilot customers invoice-billed manually.
- Public marketplace UI, fork mechanic, digest preview / showcase pages.
- Bring-your-own-key (BYOK) tier.
- Cross-tenant admin console.
- Per-tenant custom HTML templates (all verticals share one template, parameterised by config).
- Kubernetes, horizontal scaling, multi-host deployment.

Each of these is deliberately deferred. The architecture must leave clean seams for them but not implement them.

## Research Foundation

Four research documents inform this spec — read them for detail not repeated here:

- `docs/research/architecture-generalisation.md` — 5-step refactor inventory
- `docs/research/multi-sqlite-operations.md` — backup, WAL checkpoint, PRAGMAs
- `docs/research/prompt-injection-threat-model.md` — P0 path isolation, injection surface
- `docs/research/budget-cap-design.md` — `claude-code --max-budget-usd` native support
- `docs/research/vertical-pricing-model.md` — France tax math, per-vertical cost model

## Architecture

### Data Layout

Single SQLite DB with `tenant_id`/`vertical_id` scoping. Per-vertical configs as flat files. Per-vertical `claude_input/` directories for runtime isolation of LLM intermediate files.

```
data/
  digest.db                           -- single DB, all tenants scoped by tenant_id
  tenants/
    sean/news/
      vertical.json                   -- tiers, sections, style, rules, cadence, branding
      sources.json                    -- RSS feeds for this vertical
      claude_input/                   -- per-vertical intermediate files (path-isolated)
    danny/eu-regulation/
      vertical.json
      sources.json
      claude_input/
    ...
```

**Schema changes (one migration):**

```sql
-- New tables
CREATE TABLE tenants (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  primary_email TEXT NOT NULL,
  vat_number TEXT,
  country_code TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE verticals (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  config_dir TEXT NOT NULL,
  cadence_cron TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  is_active INTEGER NOT NULL DEFAULT 1,
  is_public INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Existing tables get vertical_id (backfilled to sean/news)
ALTER TABLE digest_runs      ADD COLUMN vertical_id INTEGER REFERENCES verticals(id);
ALTER TABLE shown_narratives ADD COLUMN vertical_id INTEGER REFERENCES verticals(id);
ALTER TABLE source_health    ADD COLUMN vertical_id INTEGER REFERENCES verticals(id);
ALTER TABLE digests          ADD COLUMN vertical_id INTEGER REFERENCES verticals(id);
-- run_usage already keyed by run_id -> digest_runs.vertical_id, no change needed.

CREATE INDEX idx_digest_runs_vertical      ON digest_runs(vertical_id, run_at DESC);
CREATE INDEX idx_shown_narratives_vertical ON shown_narratives(vertical_id);
```

**Why single DB:**

- At N≤10 tenants, per-file SQLite is operator aesthetics, not a requirement. Scrappy beats clever.
- One migration run per deploy instead of N. One backup file. One connection pool story.
- Transactional cross-tenant reads without ATTACH caveats (`multi-sqlite-operations.md §4` flags partial-write windows as production-disqualifying for cross-DB transactions).
- Tenant deletion is a single `DELETE ... WHERE tenant_id = X` in a transaction, still paired with the Resend-audience purge in the offboarding runbook (GDPR right-to-erasure is only satisfied when both sides scrub).
- If the DB outgrows a single file (unlikely before 50+ tenants or a genuinely separate-security-boundary customer), splitting to per-tenant files is a contained refactor with the ORM already scoped by `vertical_id`.

**Isolation stays where it matters:** per-vertical `claude_input/` directories keep LLM intermediate files separate. That's what blocks cross-tenant prompt-injection leakage. DB layout is orthogonal to that.

### vertical.json Schema

```json
{
  "id": "eu-regulation",
  "display_name": "EU Regulation Digest",
  "content_type": "regulatory",

  "schedule": {
    "cron": "0 8 * * *",
    "timezone": "Europe/Paris"
  },

  "budget": {
    "hard_cap_usd": 8.00,
    "soft_warn_usd": 5.00,
    "wall_clock_seconds": 1800,
    "max_input_articles": 200
  },

  "tiers": [
    {"key": "must_know", "display_name": "Must Know", "min_items": 3, "include_reporting_varies": true, "include_why_it_matters": true},
    {"key": "should_know", "display_name": "Should Know", "min_items": 5, "include_reporting_varies": false, "include_why_it_matters": true}
  ],

  "sections": [
    {"key": "ai_act", "display_name": "AI Act", "emoji": "🤖"},
    {"key": "gdpr", "display_name": "GDPR / DPAs", "emoji": "🛡️"},
    {"key": "dsa_dma", "display_name": "DSA / DMA", "emoji": "📱"},
    {"key": "enforcement", "display_name": "Enforcement & Fines", "emoji": "⚖️"}
  ],

  "source_metadata_fields": ["jurisdiction", "authority_type", "factuality"],
  "show_source_bias": false,

  "style_block": "Legal-technical but accessible. Lead with the binding obligation. Quote regulatory article/paragraph. Distinguish draft from enacted.",
  "editorial_rules": "Prioritise: enforcement actions, DPA decisions, codes of practice, delegated acts. Filter: think-tank opinion, non-EU regulation unless relevant to EU firms.",

  "tier_definitions": {
    "must_know": "Binding acts published, enforcement actions, major DPA decisions.",
    "should_know": "Drafts progressing, guidance documents, significant opinions.",
    "signals": "One-line mentions: consultations, workshops, adjacent jurisdictions."
  },

  "preheader_instructions": "One sentence summarising the 2-3 biggest regulatory developments. Max 150 characters.",

  "branding": {
    "digest_name": "EU Regulation Digest",
    "from_email_prefix": "eu-reg",
    "from_display": "EU Regulation Digest",
    "primary_colour": "#0B3D91",
    "logo_url": null
  },

  "resend": {
    "audience_id": "aud_danny_eu_reg",
    "domain": "digest.sean.dev"
  },

  "visibility": {
    "is_public": false
  }
}
```

**Model selection:** per-subagent model is controlled by the existing `.md` frontmatter in `.claude/agents/`. Not duplicated in `vertical.json` — two sources of truth is a trap. Per-vertical model overrides can be added later if/when a tenant genuinely needs them.

**Reserved fields:** only `visibility.is_public` (default `false`), flipped to `true` on Sean's `news` as product demonstrator. Everything else (delayed-public window, OpenRouter routing, fork mechanic) gets added when its use case exists, not pre-provisioned as dead schema.

### Code Changes

Grouped by concern, mapped to files.

#### C1 — Path isolation (Phase 0, blocks any multi-tenant work)

`mcp_server.py:143`, dispatcher prompt, all subagent prompts currently hardcode `/app/data/claude_input/`. Parameterise by vertical_id so parallel verticals don't trample each other.

- `mcp_server.py` takes `--vertical <slug>` arg, computes `DATA_DIR = data/tenants/<tenant>/<vertical>/claude_input/`
- Dispatcher prompt gets templated `{{CLAUDE_INPUT_DIR}}` injected at runtime
- All subagent prompts use relative paths resolved against dispatcher-provided working dir, or templated absolute paths

#### C2 — Dynamic MCP schema generation (from architecture-generalisation.md Step 2)

- `mcp_server.py` loads `vertical.json` at startup
- `SIGNALS_SCHEMA.properties` generated from `vertical["sections"]` list
- `SELECTIONS_SCHEMA.properties` generated from `vertical["tiers"]` list
- `TOOLS[0].description` templated from config (tier counts, section names)
- Regression test: generating schema from current `news` config must produce identical schema to today's hardcoded version

#### C3 — Jinja2 prompt templates

- `.claude/agents/select.md` → `select.md.j2` with `{% for tier in vertical.tiers %}` blocks
- `.claude/agents/write.md` → `write.md.j2` with style block + section loops + conditional `reporting_varies`
- Dispatcher renders templates at runtime with vertical config, writes rendered prompts to `claude_input/`
- Other agents (`cluster`, `recap`, `coherence`) stay as plain `.md` — they're content-agnostic

#### C4 — render.py reads tiers and sections from config

- `REGION_CONFIG` and `REGION_ORDER` removed; loaded from `vertical["sections"]`
- `calculate_reading_time`, `render_digest`, `extract_headlines`, `format_story_counts` iterate config not hardcoded keys
- `render_article` conditionally renders `bias` / `reporting_varies` based on `vertical["show_source_bias"]` and `vertical["show_reporting_varies"]`

#### C5 — prepare.py writes configurable source metadata columns

- Source CSV columns driven by `vertical["source_metadata_fields"]`
- Article index `{url, source_id, ...metadata}` generalised — no hardcoded `bias` field

#### C6 — Budget cap (from budget-cap-design.md)

Layered enforcement:

1. **Primary:** `claude-code --max-budget-usd <N>` from `vertical.budget.hard_cap_usd`
2. **Secondary:** wall-clock timeout on subprocess (default 1800s)
3. **Tertiary:** pre-flight `max_input_articles` cap in `prepare.py`

New columns on `digest_runs`: `budget_cap_usd`, `cap_hit`, `cap_phase`, `degraded_mode`.

Degradation policy when cap hit mid-run: log + alert operator, don't publish a partial digest. Optional later: fall back to previous digest with banner.

#### C7 — broadcast.py reads Resend config from vertical

- Remove `RESEND_AUDIENCE_ID`, `DIGEST_NAME`, `RESEND_FROM` env vars
- Read from `vertical["resend"]` + `vertical["branding"]`
- Single `RESEND_API_KEY` env var stays global (one Resend account, many audiences)

#### C8 — Orchestrator loop

New `bin/scheduler.py` process:

- Wakes every minute
- For each active vertical, evaluates `vertical.schedule.cron` against current time in `vertical.schedule.timezone`
- On match, spawns `python -m newsroom.run --vertical <slug>` subprocess
- **Cron jitter:** when multiple verticals share a scheduled minute, stagger by hash of vertical slug (e.g. up to ±3 min) to avoid checkpoint stampede at backup time and to smooth Claude API load
- Runs are serialised (one vertical at a time) as a safety net against path-isolation gaps (R2). Parallelism is a post-pilot concern
- Logs start/complete/error per run
- Single orchestrator process supervised by systemd (replaces per-vertical systemd timers)

#### C9 — SQLite PRAGMAs on every connection

From multi-sqlite-operations.md research. Apply to every new connection in both Python and Rust:

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA journal_size_limit = 67108864;   -- 64 MB WAL growth cap
PRAGMA synchronous = NORMAL;
```

Rust Axum handlers open short-lived per-request connections. Python run.py explicitly checkpoints `PRAGMA wal_checkpoint(TRUNCATE)` at end of run.

#### C10 — Reliability: retry, scheduling window, status page

Current pipeline fails hard if Claude API degrades. Three-layer defence:

- **Retry with exponential backoff.** Audit `claude_cli.py` retry logic; ensure transient failures auto-retry 3× with 30s/120s/300s backoff before escalating to operator alert.
- **Scheduling window, not fixed time.** `vertical.schedule` gets `run_window` (e.g. `{"earliest": "06:00", "latest": "12:00"}`). Orchestrator runs any time within window; retries on failure stay within it.
- **Status page** at `/status` on circulation server. Static HTML, manually updated during incidents. Linked from every digest footer ("delivery status"). Keeps customer expectations calibrated.
- **Proactive delay comms.** If a run is >2 hours past its window start, auto-email tenant: "Your [vertical] digest is delayed due to upstream API issues, expected delivery by [time]."

Pilot SLA language: "best-effort delivery within 24 hours of scheduled cadence; status page at digest.sean.dev/status." No 99%+ promise. Enterprise tier negotiates SLA per deal.

#### C11 — ToS footer in every digest + legal page

Every digest HTML template gets a footer:

> This digest is informational only. It does not constitute legal, financial, or professional advice. Sources are third-party; we summarise but do not verify claims beyond source attribution.

Full Terms of Service + Privacy Policy live at `/terms` and `/privacy` on circulation server. Enterprise tenants get a DPA (Data Processing Addendum) template for their procurement process.

### Rust Routing

For pilot: single-domain, path-based routing.

```
/                              -- landing page, lists public verticals
/v/<vertical_slug>/            -- vertical index (past digests)
/v/<vertical_slug>/<date>/     -- specific digest
/v/<vertical_slug>/sources/    -- sources page for this vertical
/stats                         -- global stats across all verticals
```

Backwards compatibility: `/` redirects to `/v/news/` for existing subscribers. Existing `/<date>/` URLs redirect to `/v/news/<date>/`.

**Deferred to enterprise tier:** per-tenant subdomain (`intellaw.digest.sean.dev`). Requires Caddy or Axum wildcard host routing + per-tenant domain verification. Not pilot scope.

### Competitive Landscape and Vertical Viability

Before committing engineering effort, each candidate vertical has to clear a simple bar: does the market support the price that the unit economics require? Web research (2026-04) produced sharp verdicts.

**EU regulation digest (Danny) — GO at €99-149/mo solo, €300-500/mo firm-tier.**

- Politico Pro EU / MLex occupy €5-10k/seat/yr enterprise. That ceiling is real; the gap to €0 is wide.
- Free high-quality incumbents: law-firm BD alerts (Linklaters, Bird & Bird, Osborne Clarke, Cooley, Hogan Lovells — all free, all excellent, all written to win mandates), plus FLI-funded trackers (artificialintelligenceact.eu, Digital Policy Alert, Risto Uuk's EU AI Act Newsletter).
- **Real competitive threat: AI-native compliance platforms**, not other newsletters. **Regulativ.ai**, **EuroComply**, **Daiki**, **FairNow** position as "monitor regulatory change across 47 jurisdictions," SaaS-priced, SME-sized. They've chosen workflow framing over newsletter framing — that is likely the correct move for pricing power and the most useful finding from the research. If this vertical is pursued seriously, read their positioning and decide whether to frame as "the digest your GC reads" (newsletter) or "the tracker your compliance team runs on" (SaaS). The latter prices better.
- IAPP bundles privacy/AI governance daily at ~$295/yr (individual) with membership; that's the closest direct comp at individual-subscription price.
- Buyer has budget; the blocker is attention and trust, not willingness to pay.

**EMEA B2B sales / startup intel (Hazem) — WEAK, caps at €25-40/mo solo.**

- No AI-native incumbent occupies the "Daily Upside for B2B EMEA sales" slot — there is a genuine product gap.
- But the pricing anchors are brutal: Lenny's Newsletter ($15/mo human-written, 18k paid subs), The Generalist ($22/mo), Sifted Pro (gated, probably €500-1,200/yr), Daily Upside (free, ad-supported).
- Above €40/mo you're competing with Apollo/Clay/Cognism (pipeline tools) for the same budget — and losing, because those deliver leads, not summaries.
- Real ceiling for a solo subscriber is €25-40/mo. That's below what Hazem's vertical costs to run on Sonnet-everywhere (€14/mo COGS weekly + €3 infra + €40/4 Resend Pro allocation + operator time). Not building-a-business economics.

**LogTech LatAm (Gabriel) — NO-GO at €75+, marginal at €15-25/mo.**

- Free Spanish/Portuguese trade press (T21 Mexico, MundoLogística, The Logistics World, Logistec) saturates the free tier.
- **Bloomberg Línea is €8/mo effective for general LatAm business** — that is the anchor and it has Bloomberg brand power. A niche logistics digest at €75+/mo gets laughed out of the room.
- No AI-curated product exists in this space — genuine white space — but white space at a price the buyer won't pay is not a market.
- Would require volume (thousands of subs across a fragmented bilingual market) to work at €15-25/mo. Bad economics for a solo operator.

**Conclusion:** build the platform around Danny's vertical as the revenue pilot. Hazem and Gabriel are product-shape research, not paying pilots. The question isn't "what do we charge them" — it's "are we running their verticals at all given the unit economics don't support real billing?"

### Pricing Model

**No friend pricing.** The previous draft quoted €25 → €75 step-ups at month 3. That's billing-shock by design, and if the real price would not have landed with the customer on day 1, the customer was never a paying customer — just a courtesy extending. The research makes this sharp: only Danny's vertical supports the price the unit economics require. Quoting real prices from day 1 filters that signal cleanly.

**Pilot quotes (VAT-inclusive, invoice-billed, 14-day free trial):**

| Customer | Vertical | Cadence | Price | Status |
|---|---|---|---|---|
| Danny (self) | EU regulation | Weekly | **€75/mo** | Real pilot |
| Danny → IntelLaw | EU regulation | Daily | **€299/mo** (micro-business) | If the firm is the actual payer |
| Hazem (Hi Interns) | B2B sales EMEA | Weekly | **€39/mo** | Product-shape research; run only if Hazem signs at €39 without negotiation. Otherwise park. |
| Gabriel (Flowls) | LogTech LatAm | Weekly | **Not offered for money** | Bloomberg Línea comp at €8/mo makes real pricing impossible. Either run as unpaid product-research collaboration with explicit "this ends when you stop giving feedback" framing, or do not run at all. |
| Sean | News | Daily | €0 (self) | — |

**Validation rule:** if Danny does not sign at €75/mo (self) or €299/mo (firm) on day 1, the market has spoken and this spec doesn't ship past Phase 1. The refactor is still worth doing for code health — but the multi-tenant layer stops being a business and becomes a nice-to-have. The honest decision point is *before* Phase 2, not after 6 months of sunk-cost pilot-running.

**Cost basis:** all quotes assume Sonnet across every subagent — the status quo. Haiku swap on RECAP/COHERENCE is margin upside, not baked-in.

**Resend audience cap:** free plan = 3 audiences. Sean + Danny (one audience per cadence) + Hazem = full. Gabriel triggers Resend Pro at €40/mo. Given the pricing verdict, Gabriel may not be a paid tenant at all, which defers the Resend Pro trigger.

**Published tier structure (when reaching strangers, post-pilot):**

Pricing is per-organisation, not per-seat. Email forwarding makes seat enforcement a fiction; organisation-based pricing is honest and matches how Bloomberg, HBR, and legal trade pubs actually price.

| Tier | Price (VAT-inclusive) | Who | Visibility |
|---|---|---|---|
| Solo | €75/mo | 1 person, personal use | Published |
| Micro-business | €299/mo | Small business ≤5 people | Published |
| SMB | ~€499-799/mo | Small-mid business 6-25 people | "Contact for pricing" |
| Enterprise | ~€1,500-3,000/mo | 25+ people, custom vertical, SLA | "Contact for pricing" |

Contract language: "licensed for internal [organisation] use only, not for redistribution or commercial resale." Sanity-check via LinkedIn / Crunchbase. No technical seat enforcement.

Bundle discounts: none published. Case-by-case.

VAT: customer-facing prices VAT-inclusive. Below France TVA threshold (€36,800/yr services) no VAT remitted. Above threshold, 20% remitted. Intra-EU B2B customers with valid VAT number invoiced under reverse charge. Capture VAT number at tenant creation.

## Migration Phases

Three phases. Phase 0 is a pre-flight safety pass; Phase 1 is the overnight multi-tenant build (previously split across "config extraction", "control.db", and "third/fourth vertical"); Phase 2 is observability cleanup. Assume Claude Code does the heavy lifting in Phase 1 in a single focused session. Dry-run windows are measured in days, not phases.

### Phase 0 — Path isolation + PRAGMAs + ToS footer + R3 verification

Safe, no behaviour change. Done before Phase 1 touches anything tenant-shaped.

- C1 (path isolation) + C9 (PRAGMAs) + C11 ToS footer
- Existing `news` moves to `data/tenants/sean/news/`
- `run.py` accepts `--vertical <slug>` flag, defaults to `sean/news`
- **R3 live verification:** set `--max-budget-usd 0.50` on a CLUSTER-only invocation against current subscription billing. Confirm cap fires. If it doesn't, wall-clock + pre-flight become primary enforcement and budget-cap design adjusts before Phase 1.
- **Exit criteria:** 3 consecutive successful `news` runs at the new path; R3 result recorded.

### Phase 1 — Multi-tenant build (single overnight session)

All the structural refactor in one focused execution. The components are independent enough that bundling them saves context-switching without increasing rollback surface — the entire phase is one PR, one deploy.

In-session work:

- **Schema migration.** Add `tenants` + `verticals` tables. `ALTER TABLE` adds `vertical_id` to `digest_runs`, `shown_narratives`, `source_health`, `digests`. Backfill existing rows to `sean/news`. Indexes on `(vertical_id, run_at)`.
- **C2** dynamic MCP schema generation from `vertical.json`. Regression test asserts current `news` config produces identical schema to today's hardcoded version.
- **C3** Jinja2 templates for `select.md` and `write.md`. Other subagents stay plain `.md`.
- **C4** `render.py` reads tiers/sections from vertical config.
- **C5** `prepare.py` writes source-metadata columns driven by config.
- **C6** full budget cap: native `--max-budget-usd` + wall-clock + pre-flight article count, plus the degradation paths (publish-draft, previous-digest, alert-only). Native cap already verified live in Phase 0.
- **C7** `broadcast.py` reads Resend audience + branding from vertical config.
- **C8** orchestrator loop (`bin/scheduler.py`) with cron jitter; replaces per-vertical systemd timers.
- **C10** retry with exponential backoff, scheduling window in `vertical.json`, `/status` page (static HTML + manual incident updates), proactive delay comms after 2-hour window miss.
- **Rust circulation** gets `/v/<slug>/` routing with backwards-compat redirect from `/` → `/v/news/`.
- **GDPR baseline**: `/privacy` lists Resend + Anthropic subprocessors, references SCCs for cross-border PII transfer. Tenant-offboarding runbook written (`DELETE ... WHERE tenant_id = X` + Resend audience purge). DPA template drafted.

Post-session: per-tenant dry-run windows. These are calendar-dependent, not session-dependent.

- Seed Danny's vertical once he has signed at €75/mo (or €299/mo firm). Dry-run 14 days. Quality review with Danny. Live broadcast when he approves.
- **Hazem is gated on signing at €39/mo without negotiation.** If he doesn't, park the vertical; don't run it for free just because the infrastructure supports it.
- **Gabriel is gated on the decision "run as unpaid product-research collaboration or not at all."** No paid dry-run path exists — see §Competitive Landscape.
- Resend Pro (€40/mo) upgrade trigger: third active audience (Sean + Danny + Hazem).

**Exit criteria:** Danny's live broadcast stable for 14 days; GDPR baseline reachable; Phase 0 news vertical still diff-clean.

### Phase 2 — Observability + operational polish

- Cross-vertical `/stats` dashboard in Rust — single-DB query scoped by `vertical_id`
- Daily cost rollup per vertical, weekly email to operator
- `sqlite3_rsync` backup of single `digest.db` + **rehearsed restore drill** (not just a written runbook; one real restore before telling a firm-tier customer backups exist)
- C11 full `/terms` and `/privacy` pages
- **Exit criteria:** operator can answer "what did it cost this month per vertical" in <30 seconds; incident in any vertical triggers alert email within 5 minutes; restore drill completed

### Post-Pilot — not in this spec (see Deferred Ideas for full list)

Sequenced by customer demand, not pre-committed order. Primary candidates:

- **Public showcase + vertical discovery page** (when first tenant wants delayed-public archive)
- **Claude Agent SDK migration** (when parallel execution or better rate-limit handling needed — 10+ tenants, or SLA customer)
- **Self-serve signup + Resend audience sync** (when manual onboarding exceeds ~1hr/week)
- **Stripe billing** (when invoicing exceeds ~3 customers)
- **BYOK tier** (when 3rd+ tenant requests uncapped / custom model)
- **Per-tenant subdomain routing** (when first Enterprise tier customer)
- **Fork mechanic** (when public vertical has organic interest)

## Risks and Mitigations

### R1 — MCP schema regression silently breaks Claude output

Cause: dynamic schema generation differs from hardcoded version in subtle way. Claude retries on validation failure, eats budget.
Mitigation: regression test in Phase 1 asserts generated schema equals hardcoded version. `--dry-run` verification before production cutover. Budget cap prevents runaway cost if it slips through.

### R2 — Per-vertical path isolation missed somewhere

Cause: hardcoded `/app/data/claude_input/` in a subagent prompt not caught by C1. Two verticals running concurrently trample each other's intermediate files.
Mitigation: grep audit as part of Phase 0. CI check that greps `.claude/` for hardcoded paths. Orchestrator serialises runs (one vertical at a time) in Phase 1; parallel execution is a post-Phase-2 concern.

### R3 — Budget cap may not enforce on subscription billing

Cause: `--max-budget-usd` may or may not apply when running on Claude subscription vs API. Noted in budget-cap-design.md as requiring live test.
Mitigation: **live test is a Phase 1 exit gate, not a post-hoc Phase 2 check**. Set the cap to $0.50 on a CLUSTER-only run, confirm it fires. If the native cap is a no-op on subscription, wall-clock + pre-flight article cap become primary and Phase 1 design adjusts before any new tenant is onboarded.

### R4 — Resend audience limit is 3 on current plan

Cause: Resend free tier permits 3 audiences. Sean + Danny + Hazem = ceiling. Gabriel is the fourth.
Mitigation: known constraint, not a blocker. Two acceptable paths: (a) upgrade to Resend Pro (€40/mo for unlimited audiences) when the third paid pilot joins; bake the €40/mo into that tenant's unit economics. (b) consolidate audiences by segmenting inside one audience — fragile and not recommended beyond short-term. Default to (a).

### R5 — Prompt injection via untrusted RSS once customer-added sources enabled

Cause: future verticals where tenant picks sources. Hostile feed could try to manipulate COHERENCE or WRITE output.
Mitigation: pilot tenants pick sources in conversation with operator (trusted curation). Full threat model in `docs/research/prompt-injection-threat-model.md`. Defer defensive hardening until public verticals.

### R6 — Pricing rejection ends the business case

Cause: at real prices (Danny €75/€299, Hazem €39), a candidate tenant declines. Previously this would have been softened with a friend-price fallback; that's gone. If Danny doesn't sign, the revenue thesis for this spec is falsified.
Mitigation: treat Danny's signup (or refusal) at €75/€299 as the Phase 1 go/no-go gate for Phase 2 onwards. The refactor work (Phase 0 + the schema/Jinja/budget parts of Phase 1) still ships for code-health reasons. The multi-tenant operational layer (orchestrator, `/status`, GDPR baseline) only makes sense if at least one tenant is actually paying at a price that supports it. Being clear about this up front prevents sunk-cost drift into running 3 unpaid verticals.

### R7 — AI-native compliance SaaS eats the EU-regulation vertical

Cause: **Regulativ.ai**, **EuroComply**, **Daiki**, **FairNow** are AI-native regulatory-change-monitoring SaaS products. They've already chosen the pricing-better framing (workflow/compliance, not newsletter). A digest-shaped product competes with them at a structural disadvantage.
Mitigation: before Phase 1 closes, read their positioning and make an explicit product-framing decision — "the digest your GC reads" (newsletter, priced like IAPP at ~€25/mo) vs "the tracker your compliance team runs on" (SaaS, priced at €99-299/mo). The infrastructure built in Phase 1 supports either framing; the pricing page and landing-page copy need to match whichever is chosen.

## Open Questions

1. **Danny's real answer at real price.** Does he sign at €75/mo (self) or €299/mo (firm) on day 1? If yes, Phase 1 proceeds past the refactor work into multi-tenant. If no, reconsider whether multi-tenant ships at all. This question gates the whole spec.

2. **Hazem — build or park?** At €39/mo the vertical is marginal (COGS + operator time likely losing money; see `vertical-pricing-model.md`). Does he sign at €39 without negotiation? If not, don't run the vertical.

3. **Gabriel — paid product-research or don't run?** At market-acceptable prices (€15-25/mo) the unit economics don't work. Frame as explicit unpaid collaboration with stop date, or decline.

4. **Framing decision for EU regulation vertical** (see R7): newsletter-framed (€25-75/mo) or compliance-workflow-framed (€99-299/mo). Read Regulativ.ai / EuroComply positioning before committing landing-page copy.

5. **Cron timezone handling** — Paris operator, Brazilian customer, Swedish customer. Verify Python `zoneinfo` resolution on Hetzner (Docker image has correct tzdata). 10-minute test.

6. **Backup cadence** — daily `sqlite3_rsync` snapshot of single `digest.db` sufficient, or hourly? Default: daily, consistent with existing backup pattern.

## Dependencies

- SQLite ≥ 3.47 (for `sqlite3_rsync`). Current Alpine base image: verify.
- Python `jinja2` (already present via `premailer`).
- Python `zoneinfo` stdlib (Python 3.9+, already met).
- No new Rust dependencies (single DB, existing `rusqlite` handles WAL fine).
- `claude-code` CLI version supporting `--max-budget-usd` (verify current version on deploy target).

## Out-of-Scope for This Design

These exist but are handled by their own specs, not this one:

- Cost optimisation (CLUSTER one-pass limit, Haiku swap for RECAP/COHERENCE) — separate small PR.
- Dead code removal (`claude_cli.py` async API, test_prompt.py bug) — separate small PR.
- Ruff config audit (why `claude_cli.py:67` wasn't flagged) — separate small PR.
- UI redesign of landing page / `/sources` / `/stats` — separate design.

## Deployment Notes

### Repository split

The current public `news-digest` repo stays public as the core platform (pipeline, Rust server, subagents, architecture). Tenant-specific content moves out:

- **`news-digest`** (public) — platform code, existing `news` vertical as demonstrator
- **`news-digest-tenants`** (private, new) — all tenant configs under `tenants/<tenant>/<vertical>/vertical.json` + `sources.json`. Mounted at `data/tenants/` in production (git submodule or deploy-time clone)
- **`news-digest-ops`** (private, may already exist as `$INFRA_DIR`) — deploy scripts, terraform, secret templates

Secrets (`RESEND_API_KEY`, `ANTHROPIC_API_KEY`) never touch any repo — stay in env / systemd unit overrides.

### Scaling envelope

Hardware (4 vCPU / 4 GB Hetzner) comfortably handles 100+ tenants at mixed volume profile:
- Disk: ~50MB/vertical × 100 = 5GB
- RAM: peaks ~1.5GB during pipeline runs (sequential), Rust server 100-300MB steady
- CPU: mixed mix of 5-15 min pipelines fits in 24h window; 100 × 45min news-digest-scale verticals would need parallel execution

High-volume concentration changes the math: the existing `news` vertical processes 600+ articles/day in 45 min. If 10+ tenants demand that volume, either (a) migrate to Claude Agent SDK for parallel subagent execution within Anthropic rate limits, or (b) upgrade host. Not a pilot-stage concern.

### Logging

Structured JSON logs to `/var/log/news-digest/` per vertical, rotated by `logrotate`. journald captures systemd service output (`journalctl -u news-digest-scheduler`). No SaaS log aggregation until scale genuinely demands — self-hosted Loki + Grafana on same host is the natural upgrade if pattern-matching across 20+ tenants becomes painful.

## Deferred Ideas

Consolidated list of post-pilot ideas raised during design. Each has a placeholder here so they're not forgotten. No implementation commitment — sequenced by customer demand, not roadmap gravity.

### Platform capabilities

- **Claude Agent SDK migration.** Drop-in replacement for Claude Code CLI subprocess, same token pricing (not Managed Agents — those cost $0.08/session-hour extra for orchestration we don't need). Enables programmatic hooks, better retries, parallel execution within rate limits, CI/CD testing. Trigger: 10+ tenants OR SLA customer OR reliability pressure. Effort: ~1 week.
- **Multi-model / OpenRouter routing.** Per-subagent model override via `vertical.json > models`. RECAP + COHERENCE to Gemini Flash = ~16% cost saving; primary value is provider-failover for SLA. Trigger: SLA customer OR Claude reliability degradation. Effort: ~1-2 weeks incl. prompt regression testing.
- **BYOK tier.** Customer provides Anthropic API key, pays Anthropic directly; we charge platform fee. Encrypted key storage in control.db. Trigger: 3rd+ customer requests uncapped/custom model. Effort: ~3 days.
- **Managed Agents API.** Explicitly rejected for this project. $0.08/session-hour surcharge with no benefit over Agent SDK for a pipeline that manages its own state.

### Delivery channels

- **Slack webhook.** Per-vertical `channels.slack_webhook`; broadcast fans out email + Slack. Format HTML → Slack blocks. Effort: 1-2 days.
- **Microsoft Teams webhook.** Same pattern as Slack; Adaptive Cards format. Effort: 1-2 days.
- **WhatsApp.** Meta's Business API, per-message fees, template approval. High friction, skip unless a customer pays specifically for it.
- **Push / mobile app.** Probably never.

### Content features

- **Delayed-public archive.** Per-vertical `visibility.public_after_days: 30`. Recent issues paywalled, older becomes public for SEO. Requires auth on circulation server for fresh archive.
- **Curator commentary.** Editor's note from tenant (e.g. Danny) prepended to digest before send. Human-AI hybrid. Makes digest unique vs AI-only output.
- **Multilingual rendering.** Gabriel's LatAm vertical likely wants Portuguese/Spanish sources. Translation pre-processing OR native-language prompting. Test with real sources first.
- **Data exports / API access.** Business/Enterprise feature. Export digest history as JSON/CSV for customer's internal BI. Read-only API keys per tenant.
- **Public showcase page.** `/verticals` lists all `is_public: true` verticals with subscribe CTA. Lead-gen feature once pricing page exists.
- **Fork mechanic.** "Clone this vertical as a starting point for my own." `cp -r` + tenant record insert. UX polish needed; trust/attribution model to design.

### Commerce / ops

- **Self-serve signup.** Email-link authentication, minimal onboarding flow. Trigger: manual onboarding exceeds 1hr/week.
- **Stripe billing.** Subscription management, dunning, EU VAT MOSS automation. Trigger: 3+ customers OR TVA threshold approached.
- **Reseller / white-label partner channel.** Wholesale pricing to a reseller, they retail at their markup. Channel partner brings customers. Currently no credible partner in hand.
- **Per-tenant subdomain routing.** `intellaw.digest.sean.dev` with Caddy wildcard or Axum host routing. Trigger: first Enterprise customer who asks for it.
- **Customer's own sending domain.** `alerts@intellaw.com` with customer-managed DNS. Requires Resend domain add + 3-5 DNS records on customer side. Trigger: Enterprise onboarding.
- **DPA / legal templates.** Generic DPA for EU B2B procurement. Trigger: first firm-tier customer with procurement process.

### Reliability / compliance

- **Multi-provider LLM failover.** Via OpenRouter; Claude → Gemini/GPT-4 auto-routing on provider degradation. Paired with OpenRouter migration above.
- **Formal SLA language.** 99% monthly uptime commitment with service credits. Reserve for Enterprise tier negotiated case-by-case.
- **Customer-added RSS sources.** Today sources are operator-curated. If tenants add their own, prompt injection surface opens — defences per `docs/research/prompt-injection-threat-model.md` become required. Trigger: customer demand for self-service source management.
- **Data residency / GDPR guarantees.** Hetzner is EU; current posture is defensible. Formalise with DPA when Enterprise customer's legal team asks.
- **SOC 2 / ISO 27001.** Premature for pilot. Revisit when Enterprise ACV justifies audit cost.

### Product-market-fit measurement

- **Open/click rate dashboards, reading-time instrumentation, engagement analytics.** Deferred on purpose. At three pilots, the cost of introducing subscriber tracking — cookie banners, analytics disclosure in `/privacy`, a DPIA if any of it is profiling-adjacent, and a second subprocessor if we use a hosted analytics vendor — outweighs the signal a monthly 20-minute call with each tenant gives us. Ask them directly whether it's useful, what they skipped, what they'd want instead. Revisit automated instrumentation when (a) we have 10+ tenants and direct conversation stops scaling, or (b) a tenant explicitly asks for engagement data on their own subscribers.
- **Resend already reports open/click at the audience level** without adding subscriber-level tracking beacons. If at some point a coarser signal is wanted, surfacing Resend's own numbers on `/stats` is cheaper than a full analytics stack and carries less legal overhead. Note it's coarse (per-send aggregates, not per-subscriber behaviour).

### Cost / observability

- **Haiku for RECAP + COHERENCE.** ~15% total cost reduction; low regression risk. Already on the separate cost-optimisation PR list, not pilot blocker. Pilot pricing assumes Sonnet across all subagents; the swap is future margin, not baked-in quote.
- **CLUSTER one-pass iteration limit.** ~50-70% CLUSTER variance reduction on busy days. Separate PR, flagged in `docs/research/cluster-variance-analysis.md`.
- **Self-hosted Loki + Grafana.** When log grepping across tenants becomes painful.
- **Metrics dashboard.** Per-vertical cost trend, delivery success rate, subscriber engagement (from Resend). Extends `/stats`.
- **Synthetic monitoring.** Headless probe that verifies each vertical produces a digest and sends correctly. Failure triggers alert.
