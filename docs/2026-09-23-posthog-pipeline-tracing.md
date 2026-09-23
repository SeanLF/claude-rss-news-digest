# PostHog for pipeline call tracing (bounded research, 2026-09-23)

Question: send per-model-call traces (prompt, input, output, tokens, cost, latency, tool calls, grouped
per run/stage) to PostHog AI Observability instead of the product DB. §4.3 of
`2026-09-23-data-model-design.md` already fixes what stays in SQLite regardless: `api_cost_usd` (the
budget check's synchronous `SUM(...)` read), `prompt_sha`/`input_manifest`/`output_artifact`/`result`
(resume/replay), and the product tables. This doc only asks whether PostHog is worth adding on top.

Grounding in this repo: `digest/package.json` pins `@anthropic-ai/claude-agent-sdk` `0.3.280`.
`digest/src/runner/run-stage.ts` is the single choke point — every stage calls `query()` from that SDK
once, with `tools` scoped to `Read`/`Grep` only (verified: no stage spec anywhere passes `Task`, so the
Agent SDK's own sub-agent fan-out is never exercised; WRITE's per-story fan-out is Python/TS calling
`runStage` repeatedly, not the SDK's Task tool).

## 1. TypeScript integration

**Verified** (read the shipped package, not just docs): `@posthog/ai` 8.13.1 ships a
`@posthog/ai/claude-agent-sdk` entry point built specifically for this SDK. `peerDependencies` pins
`@anthropic-ai/claude-agent-sdk: ^0.3.251` — satisfied by our `0.3.280`. It's a drop-in replacement for
`query()`:

```ts
import { query } from '@posthog/ai/claude-agent-sdk'
// query({ prompt, options, posthog: { client, distinctId, properties: { runId, stage, promptSha } } })
```

It reads the SDK's own message stream (no Anthropic client to patch, since the Agent SDK runs Claude
Code itself) and emits `$ai_generation` per model turn, `$ai_span` per tool call, `$ai_trace` per turn
with the SDK's reported cost. So the manual-capture branch of the question is moot — no hand-built
`$ai_generation` construction needed, just swap the import in `run-stage.ts:1` and thread a `properties`
bag through `runStage`'s options.

Two real limits, both client-side truncation before anything leaves the process (source: package README):
assistant/tool text capped at 200,000 UTF-8 bytes per event; tool results embedded in generation input
capped at 5,000 bytes. `enableFullAiCapture` (a `posthog-node` client option, confirmed in
`client.ts`) removes both caps — unclear from docs whether that's plan-gated (**inferred**, not
confirmed). Our inputs are "tens of KB" per the task brief, comfortably under 200 KB for ordinary
stages; a WRITE/CLUSTER branch that bundles several full fetched articles is the one case worth checking
against the cap in the PoC.

Source: `npm pack @posthog/ai` → `dist/claude-agent-sdk/index.d.ts`, `index.mjs`, `README.md`;
`npm pack posthog-node` → `src/client.ts` (`enableFullAiCapture`, `capture(): void`, `DEFAULT_NODE_HOST`).
Verified by reading the installed tarballs, not the marketing docs.

## 2. Bulk export for GEPA-style prompt optimisation

**Partly verified** (fetched `posthog.com/docs/cdp/batch-exports`; the `/docs/api/*` pages returned only
JS-shell nav to WebFetch, so the rate-limit numbers below are search-summary only, not read firsthand).

- Batch Exports: S3, BigQuery, Databricks, Postgres, Snowflake, Redshift, Azure Blob (fetched, verified).
  Backfill exists for historical ranges via the UI/API (fetched, verified). A `hogql` export model lets
  the export be an arbitrary SQL/HogQL query, not just the fixed `events` shape (found via search of a
  PostHog PR, not fetched directly — **web-search only**).
- Query API: `/api/project/:id/query` (HogQL) — 120 req/hour, default 100 rows unless you set `LIMIT`
  (up to 50k/query, then paginate) (**web-search summary, not fetched**).
- Yes: with `prompt_sha`/`run_id`/`stage` attached as custom properties (via
  `ClaudeAgentTraceOptions.properties`), a HogQL query or a scheduled Batch Export can pull every
  generation for one prompt version with full input/output text — **conditional on §3's 30-day window**.

This is the actual constraint for GEPA: pulling training data back to the Mac only works inside 30 days
of the run. That means either a nightly export job (cron/`bin/`) landing full generations before day 30,
or not relying on PostHog as the corpus of record at all — keep training inputs in the existing
`run_artifacts`/SQLite path that §4.3 already specifies, and use PostHog only for the live/aggregate view.

## 3. Retention and limits

**Verified** (fetched `posthog.com/docs/ai-observability/data-retention`): `$ai_input`, `$ai_output`,
`$ai_output_choices`, `$ai_input_state`, `$ai_output_state`, `$ai_tools` are deleted from the events
table after **30 days**, full stop — the page states no plan-based exception. Metadata (model, tokens,
cost, latency, trace id) survives on the account's normal event retention (1 year free / longer paid per
pricing pages — **web-search only**, not fetched from a retention-specific source).

Free tier: **100,000 AI-observability events/month free**, then $0.00035/event stepping down to
$0.00006 at volume (fetched `posthog.com/ai-observability/pricing`). Whether this is a pool shared with
PostHog's general 1M-event/month analytics free tier or a separate AI-specific counter is **not resolved**
— the page only says AI events "are captured as regular PostHog events and billed like them."

Our volume: 60-100 model calls/run × 1 run/day, each call producing one `$ai_generation` + 0-few
`$ai_span` (read-loop stages do call `Read`/`Grep`) + a shared `$ai_trace` per turn. Pessimistically,
call it 3-5 AI events per model call → roughly 200-500/day, 6,000-15,000/month. That's well inside the
100k free tier under either interpretation of the quota, so cost is not a decision factor here.

No PostHog-side per-event byte cap was found in the retention doc; the effective cap is `@posthog/ai`'s
own client-side truncation from §1 (200 KB / 5 KB), which happens before the network call.

## 4. Overlap with promptfoo

**Web-search summary only**, not fetched from PostHog's own eval docs. PostHog has a beta Prompt
Management feature (version/update prompts at runtime) and an Evaluations feature (code + LLM-judge
scoring of captured generations). Both genuinely overlap with what promptfoo already does on the Mac
(`~/.promptfoo/promptfoo.db`: 115 evals, 2,853 results).

Proposed line: PostHog owns **prod call traces** — what happened, cost, latency, live, queryable by
run/stage/prompt version. promptfoo stays the harness for **offline experiments** — planted-band evals,
judge transcripts, Sean's adjudications — because those need review and diffs, and git already gives
both (memory: `project_2026_09_18_deadline_fixes_deploy_and_coherence_band.md`,
`project_llm_tracing_decision.md`: "labels are small, need review and want diffs; git gives all three").
Don't let PostHog's eval feature become a second, hosted copy of that harness.

## EU hosting, privacy, and blocking

- EU option: `posthog-node`'s `host` option defaults to `https://us.i.posthog.com` (**verified**,
  `client.ts:81`, `DEFAULT_NODE_HOST`). The EU endpoint itself (`eu.i.posthog.com`) is **inferred** from
  PostHog's public docs/marketing, not found literally in the installed package — the SDK just takes
  whatever host string you pass, so setting it is a one-line config change either way.
- Privacy: article text is public publisher content, so nothing here is more sensitive than what's
  already in `data/claude_input/`. No subscriber PII passes through `runStage`'s inputs today (verified
  by the module-layering rule and by `run-stage.ts` taking only `userMessage`/`inputDir`); the only leak
  vector would be someone hand-adding a subscriber email/IP to a `properties` bag, which is a code-review
  catch, not a PostHog problem.
- Fire-and-forget: **verified** in `posthog-node`'s `client.ts` — `capture(props): void` enqueues and
  returns synchronously; the network flush happens on a timer (`flushInterval` default 5000 ms) or a
  batch-size trigger, not per call. An unreachable PostHog host queues in memory and reports through the
  configured `onError` callback; it does not throw into or block the caller. The only blocking path is
  `captureImmediate`/`shutdown()`, which `@posthog/ai`'s processor only uses when `captureImmediate: true`
  is explicitly passed — leave it unset so tracing never competes with the activity's own heartbeat/budget.

## Recommendation: adopt with conditions

- Instrument once, at `digest/src/runner/run-stage.ts`'s `query` import — not per stage.
- Nothing in §4.3 moves. `api_cost_usd` stays a synchronous SQLite read for the budget check;
  `prompt_sha`/`input_manifest`/`output_artifact`/`result` stay for resume/replay; PostHog is additive
  telemetry, never the thing a running pipeline depends on to make a decision.
- Attach `runId`, `stage`, `branch`, `promptSha` as PostHog properties on every call so exports don't
  depend on PostHog's own trace IDs.
- If GEPA needs the corpus, export within the 30-day window (HogQL pull or a scheduled Batch Export) —
  don't let PostHog become the system of record for training data.
- Use the EU host, plain `client.capture()` (never `captureImmediate` on the hot path), no PII-bearing
  properties.
- Keep promptfoo as the offline-eval harness; PostHog is prod observability only.

**Smallest PoC:** instrument one real digest run end-to-end (`npm i @posthog/ai posthog-node`, swap the
import, tag every call with `runId`/`stage`/`promptSha`), then from the Mac run one HogQL query pulling
every `$ai_generation` for that `run_id` with full `$ai_input`/`$ai_output`, and diff its per-call token
totals against that run's own `run_usage.api_cost_usd` rows. One run settles: does the wrapper actually
capture every stage (including a `read-loop` COHERENCE call with tool spans), does the 200 KB/5 KB cap
clip any real input, and does the query API round-trip full text back losslessly. Free-tier cost;
no code merge required to run it.
