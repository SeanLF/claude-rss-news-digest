# LLM tracing backends: evaluated, and none adopted

*2026-08-30. Stale by default; free tiers move monthly and the emitter is in beta, so treat every
number below as "checked on this date" rather than "true". Every vendor claim is marked VERIFIED
(read on a vendor-controlled page) or INFERRED or COULD NOT CONFIRM.*

## Verdict

**Adopt nothing. Commit the two `run_usage` columns already sitting in the working tree, add one
analytics query over them, and re-open this question only if the trigger conditions in §11 fire.**

The reasoning is not "hosted observability is bad". It is narrower and harder to argue with: the
specific gap that prompted this doc is **the one thing an OTLP backend cannot see**, because Claude
Code does not emit it. Everything else a tracing backend would show is either already in SQLite or
already in `run_artifacts` at higher fidelity.

---

## 1. The gap, and the fact that it is already closed

`run_usage` recorded which model ran but never how it was configured. On 2026-08-30 that cost a
whole measurement: a 60-run COHERENCE model comparison ran every arm with
`thinking: {"type": "disabled"}` inherited from `orchestrate.py:66`, and thinking turned out to be
a **larger** lever than model choice (recall 0.761 to 0.919, p=0.002, and 26% cheaper). Full write-up
in `docs/2026-08-30-health-check-and-clustering-sota.md`; the reusable lesson is
`docs/lessons/measure-the-config-before-the-model.md`.

The fix already exists and is uncommitted (and was still being extended while this doc was being
written, so the file list is a snapshot, not a manifest):

```
?? migrations/20260830210000_add_run_usage_request_config.sql   ALTER TABLE ... ADD COLUMN thinking TEXT / effort TEXT
 M newsroom/src/db.py               record_usage 9 -> 11 columns, .get() so old callers write NULL
 M newsroom/src/usage.py            _thinking_label() flattens ThinkingConfig to one queryable token
 M newsroom/src/orchestrate.py      thinking=spec.thinking or _THINKING, effort=spec.effort
 M newsroom/src/thread_synthesis.py the second usage-recording path, named _THINKING so the value
                                    sent and the value recorded cannot drift apart
```

That is one additive migration, two nullable columns, and the resolved value actually sent to the
SDK rather than the frontmatter text. **This doc is therefore not "null option versus vendor". It is
"the null option shipped this morning; does a hosted backend buy anything on top of it?"**

## 2. The decisive constraint: Claude Code does not emit `thinking`

VERIFIED against `code.claude.com/docs/en/monitoring-usage` and
`code.claude.com/docs/en/agent-sdk/observability`, checked 2026-08-30.

Claude Code emits three OTel signals behind `CLAUDE_CODE_ENABLE_TELEMETRY=1`: metrics, log events,
and (behind a second flag, `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`) traces. The `claude_code.llm_request`
span is genuinely rich: model, `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`, `duration_ms`, `ttft_ms`, `request_id`, `stop_reason`, retry `attempt`,
`success`, `agent_id`/`parent_agent_id`.

It does not carry extended-thinking configuration, and it does not carry a thinking-token count.
Two feature requests asked for exactly that and both were **closed as "not planned"**:
[#31585](https://github.com/anthropics/claude-code/issues/31585) (thinking tokens in metrics) and
[#46118](https://github.com/anthropics/claude-code/issues/46118) (`OTEL_LOG_THINKING=1` exporting
thinking content as `llm.thinking`). Even the escape hatch is closed: `OTEL_LOG_RAW_API_BODIES=1`
exports full request/response JSON with **extended-thinking content redacted**.

So the sequence is: the pipeline's own telemetry could not show the confound; a hosted backend
could not have shown it either; and the fix in both cases is the same two columns.

The upstream standard is no help either. VERIFIED against the OpenTelemetry GenAI semantic
conventions: `gen_ai.request.reasoning.level` **does** exist at Development stability (merged
[PR #258](https://github.com/open-telemetry/semantic-conventions-genai/pull/258), June 2026) and its
prior-art table maps it to Anthropic's `output_config.effort`. But there is no attribute anywhere for
an on/off thinking toggle, and the numeric budget was **deliberately scoped out**, with the dotted
name chosen specifically to reserve `gen_ai.request.reasoning.budget` for a later version. Practical
consequence if anyone instruments this by hand: do not name a custom attribute
`gen_ai.request.reasoning.budget`, because that name is informally reserved and a future spec version
will collide with it.

**One unresolved conflict, stated rather than smoothed over.** One research pass reported that
Claude Code emits a custom `effort` attribute (`low|medium|high|xhigh|max`) on the cost counter,
token counter, and `api_request` events. Two other passes enumerated the span attributes and did not
find it. The claim is plausible and the sources are not strictly contradictory, since it is a claim
about metrics and events rather than spans. It is **unverified**, and it concerns the lesser half of
the gap: `effort` is `spec.effort`, while the 60-run confound was `thinking`. Nobody found a
`thinking` attribute anywhere.

## 3. The incident test, run honestly

A 2026-07-26 adversarial review killed a PostHog recommendation by asking one question of every
documented incident: would this tool have caught it? The answer was no for all of them, because this
system fails silent-wrong rather than loud-crash. That verdict produced `newsroom/src/run_health.py`
instead. **That verdict was about error tracking, and it does not automatically transfer to tracing,
so here is the same test re-run for per-call trace data.**

The question asked of each: *would a span carrying model, request config, tokens, latency, cost, stop
reason and (optionally) the response body have surfaced this?*

| # | Incident | Surfaced by per-call traces? |
|---|---|---|
| 1 | 2026-08-30 thinking-config confound (the driver) | **No.** No attribute exists; see §2 |
| 2 | 2026-06-16 CLUSTER hit the 32k output ceiling | **Yes.** `stop_reason=max_tokens` + `output_tokens` at the ceiling is literally a span attribute |
| 3 | 2026-07-11 preheader 152 vs 150 chars | No. Python schema validation, raised loudly with the exact message |
| 4 | 2026-07-16 duplicate cluster label, identical cards | No. Render-time collision on a non-unique key; `selections.json` held two correct summaries |
| 5 | Run 244 linker returned quoted ids | No. The call succeeded; `isinstance(int)` discarded the results downstream |
| 6 | `HEALTH_ALERT_EMAIL` dead for months | No. Terraform wrote `DIGEST_ALERT_EMAIL`; no LLM call involved |
| 7 | `der_spiegel` 0 usable articles for 15 runs | No. HTTP 200, 20 stale English entries. Not an LLM call |
| 8 | Run 247 leaked article ids to subscribers | No in practice. Content capture is off by default, and nothing would flag it |
| 9 | Run 271 self-correcting two-object reply | **Yes, in principle.** The raw body holds both objects |
| 10 | Run 280 blanked `why_it_matters` in 38% of the digest | No. COHERENCE calls were healthy; `_REPAIRABLE_FIELDS` in `merge.py` is the cause |
| 11 | CLUSTER junk drawers, 23% of shipped clusters | No. Deterministic Python join; CLUSTER has not sent a prompt to a model since 2026-07-01 |
| 12 | The Hindu 403 / `france24` IPv4-only | No. Feed fetch layer |
| 13 | Cost drift: $5.5/run against a recorded $2.5-3.0 | **Yes**, but `cost-stage-share-drift.sql` already produced exactly that table |

**Two of thirteen, and one of thirteen net-new.** Tracing scores better than error tracking did, and
that is worth saying plainly: this is not a repeat of the PostHog verdict, it is a weaker version of
it. But the accounting is unkind on inspection.

- **#9 is already covered.** `run_artifacts` stores the stage output character-exact, which is how
  run 271 was actually diagnosed. Getting the same thing from a trace requires
  `OTEL_LOG_RAW_API_BODIES=1`, which is simultaneously the single largest credential-exposure surface
  in the whole design (§8).
- **#13 is already covered**, by the very query that produced the finding.
- **#2 is the one genuine win, and it is not the token ceiling itself.** That failed loudly. The win
  is that a trace exported off-box would have **survived `abort_run` deleting the run's forensics** on
  2026-06-16. That is a durability property, not an analysis property, and `run_artifacts` was built
  in response to the same class of loss.

The remaining eleven share a shape: the model call was fine and something downstream in Python was
not. That is the shape `run_health.py` was built for, and the shape the new
`analytics/queries/blanked-why-it-matters.sql` was written for this week.

## 4. The strongest argument in the whole file, and it is measured

The 2026-08-30 sweep harness **already recorded the configuration**. MEASURED, just now, over
`scratch/coherence-models/out/*.jsonl`:

```
94 records, every one carrying a "thinking" field
('claude-opus-5','disabled') 24   ('claude-sonnet-5','disabled') 24
('claude-sonnet-4-6','disabled') 12  ('claude-haiku-4-5','disabled') 10
('claude-opus-5','adaptive') 12   ('claude-sonnet-5','adaptive') 12
```

The first sweep's 60 runs all carried `thinking: "disabled"` in plain text, in the exact file the
analysis was run against, from record one. It still took a separate prompt audit to notice.

**The failure was not that the data was missing. It was that nobody queried it.** A hosted dashboard
is a nicer renderer for a field that was already there and already ignored. Buying one to fix a
noticing problem is buying the wrong thing, and this project has a lesson about precisely that shape:
`docs/lessons/best-practices/a-detector-nobody-reads-is-not-a-detector.md`.

## 5. Scale: the free-tier comparison is nearly moot

MEASURED against the prod clone:

- **18.5 model calls per run**, roughly **555 per month** across 30 runs.
- **~500 KB per run** of character-exact stage inputs and outputs in `run_artifacts`
  (1,074 rows / 38.4 MB over 75 runs), so under **15 MB/month** even if every artifact were shipped as
  span content.
- Wall clock 16.1 to 22.2 minutes per run; ~18.9 minutes of that is model time.

Every free tier below is three to four orders of magnitude larger than this workload. **Nothing here
is chosen or rejected on quota.** The free-tier column is a check for disqualifying cap *behaviour*,
not a capacity comparison.

## 6. The candidates

Checked 2026-08-30. Retention is the column that actually differentiates, because the question this
project asks is "how does this month compare to last month", and a 14-day window cannot answer it.

| Vendor | Native OTLP | Free tier | Retention | At the cap | Self-host on a CX23 |
|---|---|---|---|---|---|
| **Honeycomb** | Yes, gRPC + HTTP/protobuf + HTTP/JSON; one header | 20M events/mo, unlimited seats | **60 d** | Email first; throttle (accepts 1 in 10) only after two consecutive over-months + 10 days grace | No |
| **Axiom** | Yes; needs `Authorization` **and** `X-Axiom-Dataset` | 500 GB/mo, "permanent, no credit card" | 30 d | Pause, per pricing page; the limits page says charges apply. Conflicting | No |
| **Pydantic Logfire** | Yes, and the only one ingesting **all three signals** | 10M records/mo, 1 seat + 2 guests | 30 d | Hard stop, "you can't owe us anything on the personal tier" | Helm chart is open; **container images are Enterprise-only** |
| **Grafana Cloud** | Yes, HTTP only | 50 GB traces/mo, 3 seats, no card | **14 d** | Hard cap, `RESOURCE_EXHAUSTED` | Tempo monolithic + Grafana = two processes; no published floor |
| **Sentry** | Yes but **open beta**; **OTLP metrics unsupported** | 5k errors + ~5M spans (unverified), 1 seat | 30 d | Hard drop, 429, no surprise bill | **4 CPU / 14 GB gate, 56 services.** No |
| **New Relic** | Yes; US/EU/JP endpoints, dualstack | **100 GB/mo perpetual**, 1 full user | **7 d for full traces** | **Ingest stops AND you lose platform access until the 1st** | No |
| **Dash0** | Yes | 14-day trial; **permanent free tier could not be confirmed** | 30 d spans | Pauses at a hard cap | No |
| **Uptrace** | Yes, cleanest env-var story | 50 GB/mo, unlimited users, no card | 28 d | **Keeps ingesting and bills you** | 2 cores + 4 GB documented, i.e. the whole box |
| **SigNoz** | Yes | **No free tier.** $49/mo, trial deleted after 7-day grace | 15 d | n/a | **4 GB Docker minimum + ClickHouse.** No |
| **Langfuse** | **Traces only.** No `/v1/logs` route; `/v1/metrics` is a no-op stub | 50k units/mo, 2 users | 30 d | **Could not confirm** | Postgres + ClickHouse + Redis + S3; ~11 CPU / 25.5 GiB. No |
| **Arize Phoenix** | **Traces only**, port 6006 | Self-host uncapped; **Phoenix Cloud limits could not be confirmed** | n/a | n/a | Docker path is one container + SQLite; **default Helm pulls Postgres**. See §7 |
| **PostHog** | Receiver exists, but it is an **AI-span filter** | 100k AI events/mo, **1-year retention** | 365 d | Hard stop, no card | 4 vCPU / 16 GB, **officially unsupported** |
| **Braintrust** | Traces + logs; needs a proprietary `x-bt-parent` routing header | 1 GB/mo (**bytes, not spans**), 14 d | 14 d | Hard block without a card; **uncapped billing with one** | Enterprise-only, ~12 vCPU / 27 GiB |
| **Jaeger / Tempo / OpenObserve** | Yes | Self-host | local | n/a | See §7 |

Two structural findings matter more than any row.

**Nobody's LLM dashboard lights up.** Claude Code never emits `gen_ai.usage.*`; tokens ride on
custom names (`input_tokens`, `output_tokens`, `cache_read_tokens`). Every vendor's prebuilt cost and
token panel keys on the spec names and renders empty. Sentry's is worse than empty: it does not read
a cost attribute, it *computes* cost by multiplying reported tokens against a price catalogue, and
its documented behaviour on no match is to report **$0.00**. That is a broken instrument exiting
zero, which is the exact failure shape this project's own standards forbid. The differentiator
therefore collapses from "does it understand GenAI" to "does it store and let me query an arbitrary
attribute", which is a much lower bar that Honeycomb (schemaless), Logfire (`attributes` as queryable
jsonb) and Axiom clear, and which Langfuse fails in a specific way worth recording: unmapped
attributes land in `metadata.attributes` and are, in its own docs' words, **not queryable**.

**PostHog is out on structure, not preference.** Its OTLP receiver exists at
`https://us.i.posthog.com/i/v0/ai/otel`, but per its own docs it drops server-side any span whose name
and attribute keys do not start with `gen_ai.`, `llm.`, `ai.` or `traceloop.`. Claude Code's root
`claude_code.interaction` span carries only `user_prompt`, `user_prompt_length`,
`interaction.sequence` and `interaction.duration_ms`, so it is dropped and every trace is orphaned.
Ironically PostHog has the best retention on the list at a full year.

## 7. Self-hosting: ruled out by arithmetic, again

Production is a Hetzner CX23, 2 vCPU and 4 GB, already running the newsroom container and the Rust
circulation server. `docs/2026-07-01-improvement-roadmap.md:126-131` (item C7) reached this exact
conclusion two months ago and said "**do NOT self-host SigNoz/ClickHouse on the 4GB CX23**". Nothing
has changed except that the numbers are now sourced.

SigNoz documents a 4 GB Docker minimum before your own workloads exist, and needs ClickHouse, whose
own vendor says total memory "shouldn't be below 8GB". Uptrace documents 2 cores and 4 GB, which is
the entire machine. Langfuse requires Postgres **and** ClickHouse **and** Redis **and** S3-compatible
storage, summing to roughly 11 CPU and 25.5 GiB, about six times the box; its old single-container
mode is EOL. Sentry hard-gates its installer at 4 CPU and 14,000 MB across 56 compose services.

The lighter options are real but do not change the answer. OpenObserve is a genuine single AGPL Rust
binary with SQLite and local disk, actively maintained, and could physically run here; but
`ZO_MEM_TABLE_MAX_SIZE` defaults to **50% of system RAM**, its docs say it "will try to use all the
available RAM", and its own guidance asks for at least 8 GB. It would need three memory caps pinned
as load-bearing config. Jaeger all-in-one with the Badger backend is the lightest thing that survives
a restart, and has no LLM view at all. Tempo needs object storage for a supported configuration plus
Grafana in front, so it is two processes for the worst usefulness per megabyte.

**The framing that settles it is not the resource table.** RAM on this box is the documented binding
constraint; it is the same constraint that ruled out ONNX and embeddings. A co-hosted trace store
that spikes during a digest run takes down **the pipeline**, not just the dashboard. Observability
that can kill the thing it observes is a downgrade, and it makes the failure correlated rather than
independent, since both restart together.

## 8. Security, and one disqualification

Traces here would carry news article text and prompt content, and the pipeline authenticates with
`CLAUDE_CODE_OAUTH_TOKEN`.

The good news is that the emitter is safe by default. `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`,
`OTEL_LOG_TOOL_CONTENT` and `OTEL_LOG_RAW_API_BODIES` all default off, prompts ship as `<REDACTED>`,
and the OAuth token is never a span attribute. **The entire credential-exposure surface is one env
var**, `OTEL_LOG_RAW_API_BODIES`, which emits full Messages API JSON including conversation history.
Leave it off. That control is identical across every vendor, which means server-side scrubbing is not
a real differentiator: none of the candidates scrubs anything on the plain-OTLP path, because every
scrubbing feature named in their docs lives in that vendor's own SDK, upstream of the exporter.
Sentry is the only one with default-on server-side scrubbing, and CVE-2025-65944 showed its two
layers sharing one matcher and failing together (Node SDK only, `sendDefaultPii: true` required, so
it would not have hit a Python plus stock-OTel setup; a reason not to treat the scrubber as the
control, not a reason to reject Sentry).

Breach history worth recording: New Relic publishes NR23-01 (Nov 2023, staging environment, small
percentage of customers), which is the disclosure behaviour you want. Grafana had a July 2025 Mimir
memory-corruption bug that **did** leak data across a small subset of Metrics customers, plus a 2026
npm compromise reaching its GitHub. PostHog published its own post-mortem of the Shai-Hulud 2.0 npm
compromise of `posthog-js`/`posthog-node`/`@posthog/ai`; `posthog-python` was unaffected.

**Braintrust is disqualified, and I verified this myself rather than taking it from research.**
Rendering `trust.braintrust.dev/updates` (a Vanta SPA that returns an empty shell to a plain fetch)
gives the 2026-05-29 conclusion post verbatim:

> An employee credential compromise resulted in unauthorized access to a temporary EC2 instance used
> during a Control Plane database migration. As part of the migration workflow, AI provider
> credentials were temporarily exposed while being moved between storage systems. Forensic analysis
> indicates the attacker accessed a backup created during that process, which contained **decrypted
> AI provider credentials**.

> Forensic analysis indicates the attacker's activity was **limited to Anthropic API keys** contained
> in the migration backup.

> One customer was initially identified as impacted during our investigation. Following our security
> bulletin and customer outreach, **30 customers reported suspicious activity** tied to AI provider
> keys stored in Braintrust.

The contemporaneous press ([TechCrunch](https://techcrunch.com/2026/05/06/ai-evaluation-startup-braintrust-confirms-breach-tells-every-customer-to-rotate-sensitive-keys/),
[SecurityWeek](https://www.securityweek.com/ai-firm-braintrust-prompts-api-key-rotation-after-data-breach/))
reported one confirmed customer plus three with suspicious spikes, so the final numbers are worse
than the coverage. The exposed class of credential is exactly the class this pipeline holds. Separately
Braintrust has published two of its own advisories about instrumentation recording customer API keys,
one of them in its **Claude Agent SDK wrapper**, which is the SDK this repo uses. Both affect their
proprietary wrappers rather than the plain-OTLP path, so it is a track record concern rather than a
direct hit, but combined with the breach it is enough. Do not adopt Braintrust.

## 9. The one-shot flush problem is real, not a footnote

The newsroom is a one-shot container that exits after ~19 minutes. VERIFIED: Claude Code's
`OTEL_TRACES_EXPORT_INTERVAL` defaults to 5000 ms (metrics 60000 ms), and its own docs say that on a
clean exit "it attempts to flush pending data, but the flush is bounded by a short timeout, so spans
can still be dropped", and that anything in the batch buffer is lost if the process is killed first.
No vendor differentiates here; the OTel spec requires `Shutdown` to force-flush, and nothing states
that Claude Code calls shutdown.

Three project-specific aggravations, which is why this is not a footnote:

1. **There is no seam to put a flush in.** `run.py`'s `main()` has `try/except/else` but **no
   `finally`**, and there is no `atexit` and no signal handler. The failure path re-raises before
   reaching cleanup. A flush covering both paths is new code on the outermost boundary, not a config
   line.
2. **There is precedent for exactly this loss.** `CLAUDE_CODE_EAGER_FLUSH=1` is already set in
   `docker-compose.yml` because the CLI buffered session-JSONL writes and a one-shot container lost
   them (`docs/lessons/integration-issues/buffered-sdk-logs-need-eager-flush-before-reading.md`).
3. **Never set the `console` exporter.** The SDK uses stdout as its channel; C7 already recorded this.

And a vendor-specific hazard worth naming because it is this project's least favourite failure shape:
New Relic's OTLP endpoint returns success after only minimal synchronous validation, then validates
asynchronously and "drops the entire record when a single attribute is invalid", surfacing failures
only as `NrIntegrationError` events you must go and query for. Combined with a 1 MB payload cap, the
failure mode is a clean 200, a clean container exit, and silently discarded spans.

## 10. What a hosted backend would buy beyond `bin/analytics`

`bin/analytics` is 15 stored SQL queries over a read-only SQLite clone, each carrying a
`QUESTION / WHY / CAVEAT / PARAMS` header, invoked daily and named directly in the run-health alert
email. `run_usage` already holds per-stage model, four token counts, API-equivalent cost and
`duration_ms`; the uncommitted change adds `thinking` and `effort`.

Honestly stated, a hosted backend would add:

- **A waterfall view of the five stages**, with per-turn spans inside multi-turn stages. WRITE
  averages 355 seconds and 16.5k output tokens per run, so it is plainly multi-turn and `run_usage`
  flattens that to one row. This is the only genuine capability gain on the list.
- **Off-box durability**, which mattered exactly once, on 2026-06-16.
- **Alerting on span attributes**, which `run_health.py` already does on outcomes, better.
- **Dashboards we would rarely open.** Said plainly because it is true: the run-health alert already
  points at `bin/analytics run run-reliability`, and the 2026-08-30 measurement work was done in
  SQL against a clone, not in a UI.

Against that, note what tracing platforms cannot offer. `run_artifacts` stores **character-exact**
stage inputs and outputs, uncompressed, with no sampling and no truncation, ~500 KB per run, and its
own migration header calls it a "Durable per-run trace store". `bin/trace` reads it. Every hosted
platform samples, truncates (Braintrust 10 MB, Grafana 5 MB/trace, PostHog 4 MB), and caps retention
at 14 to 60 days, against a table with no retention policy at all. **For replay fidelity, which is
what actually diagnosed runs 271 and 235, the local store is strictly better.**

## 11. Recommendation, and what would reverse it

**Adopt no backend.** Concretely:

1. **Commit the `thinking`/`effort` migration and its plumbing.** It closes ~95% of the stated gap
   for one additive migration, no new dependency, no new vendor, and no data leaving the box.
2. **Add `analytics/queries/config-drift.sql`**, in the house `QUESTION / WHY / CAVEAT / PARAMS`
   shape, grouping cost and recall proxies by `(subagent, model, thinking, effort)` across two run
   windows, the way `cost-stage-share-drift.sql` already does for `model` alone. This is the part
   that is genuinely missing: §4 shows that recording the value is not the same as noticing it
   change, and none of the 15 existing queries reference the new columns.
3. **Consider promoting the config to a `run_health.py` invariant later**, once there is a base rate.
   A rule that fires when a stage's resolved `thinking` differs from the previous run's is the
   mechanism that actually prevents a repeat, and it costs nothing to evaluate. Not now; the rule
   needs data first, per the module's own "never a threshold someone tuned to taste" standard.

**Re-open this if any of the following becomes true.** These are the honest triggers, not hedges:

- Anthropic ships a thinking or reasoning attribute in Claude Code's telemetry, reversing #31585
  and #46118, **or** starts emitting `gen_ai.request.reasoning.level` and `gen_ai.usage.*` under the
  spec names. That single change flips most of §2 and §6 at once.
- Claude Code's tracing leaves beta and per-turn visibility inside WRITE or SELECT becomes a
  question someone actually needs answered, rather than one that would be nice to have.
- The pipeline becomes multi-tenant or multi-vertical, so that "which stage on which tenant" stops
  being answerable by one SQLite file. (Currently ruled out; see the multi-tenant decision.)

**If that day comes, the shortlist is Honeycomb, then Logfire, then Axiom.** Honeycomb for 60-day
retention, unlimited seats, the only gRPC ingest, the gentlest overage behaviour on the list, and a
schemaless store where Claude Code's non-spec `input_tokens` is exactly as queryable as the spec name.
Logfire if the metrics and logs signals matter more than trace retention, since it is the only
candidate that ingests all three and the only one where an arbitrary attribute is both visible and
queryable in SQL. Axiom on raw free-tier headroom, with the caveat that its docs never actually show
the `OTEL_EXPORTER_OTLP_*` env vars, so "env vars alone" is inferred and worth a five-minute spike
before committing. Grafana loses on 14-day retention; Sentry on OTLP being beta with metrics
unsupported and a cost panel that reports $0.00; New Relic on losing platform access at the cap;
Uptrace on billing past the cap and 12 commits in six months; SigNoz on having no free tier at all;
PostHog, Langfuse and Phoenix on ingest shape; Braintrust on §8.

## 12. Prior art in this repo, and what changed since

This question was already asked. `docs/2026-07-01-improvement-roadmap.md:126-131` filed it as **C7**,
scoped it correctly ("wiring is pure env"), named Grafana/Honeycomb/Dash0, ruled out self-hosting on
the CX23, flagged the console-exporter trap, and judged that per-stage latency plus `/stats` "covers
~80% of the need". It was deferred at line 159 as "needs a backend".

Two months on, with the backends now actually priced and the emitter now actually read: **C7's 80%
estimate was too low, and its one open question has a worse answer than expected.** The remaining
20% it gestured at was per-turn spans, which is still the only real gain; and the specific thing that
went wrong since, the thinking confound, is provably outside what any of these backends can ingest.
C7 should be closed as decided rather than deferred.

## Method and limits

- Vendor facts come from four parallel research passes restricted to vendor-controlled pages, on
  2026-08-30. Per this project's standard, subagent findings are claims, not conclusions: the two
  load-bearing ones were re-verified directly. The Braintrust breach was confirmed by rendering the
  vendor trust centre here (§8) and cross-checked against two press reports. The measured local
  numbers in §1, §4, §5 and §10 were queried directly against the prod clone in this session.
- The three research passes disagreed on a verdict (Honeycomb, Logfire, Axiom), each reasoning
  correctly within its own subset of four. §11 arbitrates across all thirteen.
- Sentry's free-tier event volumes, Dash0's permanent free tier, Langfuse's behaviour at its 50k
  cap, and Phoenix Cloud's limits are all **could not confirm**. None is load-bearing, since nothing
  is being adopted, but do not let a comparison site fill them in later; the figures circulating for
  "Phoenix Cloud" appear to restate Arize AX's, a different product.
- Not tested: whether Claude Code's beta trace exporter actually flushes on exit from this container.
  If anyone revisits this, that is the first negative control to run, because everything downstream
  of it assumes the tail arrives.
