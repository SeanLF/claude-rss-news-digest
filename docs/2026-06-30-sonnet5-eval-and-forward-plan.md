# Sonnet 5 evaluation + forward plan — handoff (2026-06-30)

Format mirrors Sean's session handoffs: Landed / Findings / What's left / To resume / Decisions needed.

## Landed this session
- **Sonnet 5 (`claude-sonnet-5`) per-stage A/B vs Sonnet 4.6**, on a fixed `data/claude_input/` snapshot, reusing `orchestrate.run_stage` with a `model_override` (exact prod invocation; only model/config varied). Harness re-runnable; **prod pipeline untouched** (scratch-side overrides; snapshot restored from `.ORIGINAL` backups).
- **Memory hygiene** (Phase 0): MEMORY.md 4,816→3,309 tok; closed items → `archive.md` (not injected); findings → `project_sonnet5_eval.md`; fixed the RECAP-on-Haiku contradiction (was in 2 places).
- Artifacts: `scratch/sonnet5_write_ab.py` (stage A/B w/ `STAGE`/`MODELS`/`THINKING`/`EFFORT`/`SPEC_OVERRIDE`/`TAG` env knobs), `scratch/why_judge_drafts.py` (filler-rate), `data/claude_input/sonnet5_ab/` (drafts + summaries).

## Findings (all n=1 unless noted — NOT yet repeatable)
- **CLUSTER**: cost-neutral (+3.7%, $1.37→$1.42), **2.5× faster** (12.5→5 min). Partition 267→298 clusters (finer). **"No regression" claim STRUCK** — rested on ARI-vs-one-gold (the ill-posed metric we discredited). Quality unproven; needs a **task-grounded** test (does the digest come out equal/better), not ARI.
- **WRITE config matrix** (fixed input; cost = SDK API-equiv $; filler via why-judge, judge pinned sonnet-4-6):
  | thinking | effort | cost | wall | filler |
  |---|---|---|---|---|
  | 4.6 disabled (incumbent) | — | $0.45 | 133s | 0/17 |
  | 5 disabled | high (default) | $0.68 | 85s | 1/17 |
  | 5 adaptive | high | $1.08 | 292s | — |
  | 5 adaptive | low | $0.55 | 61s | 2/16 |
  | 5 adaptive | **medium** | $0.82 | 159s | **0/17** |
  - The first reported "+51–140% premium" was **misconfiguration** (`thinking:disabled`+`effort:high`), not the model. `thinking:adaptive` kills the Write×3 self-revision pathology; `effort` is the dominant cost lever (Sonnet 5 default = high).
  - **Citation recall**: 4.6 ~98% (dumps whole cluster as `sources`), Sonnet 5 ~80% (cites only what backs claims). 0 hallucinated IDs either; same 1/17 single-source count. Precision-vs-coverage = a **product decision**, promptable.
  - Filler deltas (0–2 lines / 16–17) are within judge noise (precision 0.938) — quality not separable at n=1.
- **SELECT, thread-synthesis**: UNTESTED (highest-value next; synthesis has the gold-free audit net).

## The keystone blocker (Phase 1) — and a latent bug regardless of Sonnet 5
`orchestrate.py` hardcodes `_THINKING = {"type":"disabled"}` and **never passes `effort`** → the `effort: medium` in `.claude/agents/*.md` frontmatter is **INERT** (silently ignored). The SDK `ClaudeAgentOptions` exposes `effort`, `thinking`, `max_thinking_tokens`, `task_budget`, `output_config`.

### Phase 1 implementation recipe (TDD; mostly offline-verifiable, no model spend for the mechanism)
1. `claude_cli.py::_build_options` + `run_agent`/`run_sync`: add `effort: str | None = None` param; if set, `kwargs["effort"] = effort` before `ClaudeAgentOptions(**kwargs)`. (Confirmed field exists.)
2. `orchestrate.py::AgentSpec`: add `effort: str | None` and `thinking: dict | None` fields.
3. `orchestrate.py::parse_agent_spec`: parse `effort` and (optionally) a `thinking` key from frontmatter into the spec.
4. `orchestrate.py::_invoke_agent`: pass `effort=spec.effort`; use `spec.thinking or _THINKING` (so the **default stays `{"type":"disabled"}`** — preserves the deliberate CLUSTER-32k-ceiling fix).
5. **Unit test (no model call)**: build options for a spec with `effort: medium`/`thinking: adaptive` frontmatter; assert the `ClaudeAgentOptions` carries them. Match `newsroom/tests/test_orchestrate.py` style.
6. `usage.py:23 _PINNED_MODEL_IDS`: add `claude-sonnet-5` (logged "model drift" every run).

### ⚠️ Behavior-change wrinkle to decide BEFORE merging
Activating the frontmatter makes existing stages run at their declared `effort: medium` instead of the current unset/default(high). That changes current prod cost/quality. Options: (a) plumb mechanism but set frontmatter to match current behavior (omit effort) and tune deliberately later; (b) accept medium and validate with a full pipeline run + why-judge/coherence before deploy. **Recommend (a)** — ship the lever inert-by-default, then tune per stage with the task-grounded eval.

## To resume (fresh session — recommended for Phases 1–3)
1. **Phase 1**: implement the recipe above (TDD, unit tests, no model spend). Decide the wrinkle (recommend (a)).
2. **Phase 2**: build the **task-grounded clustering eval** — run SELECT→WRITE on each model's clusters, score the *digest* (why-judge filler + coherence drop count), commit to n≥3. This is the measuring stick.
3. **Phase 3**: with Phase 1+2, re-run the Sonnet 5 decision per stage, properly tuned + repeated. Fold in the recall product call.
4. **Phase 4** (separate initiative): scope graph-first redesign — attach articles to the persistent story-graph (fold CLUSTER + thread-linking), decouple grouping from ranking, bounded graph/DB tools **within the existing small-stage Python-orchestrated shape** (keep many-small-prompts; NO LLM orchestrator).

## Exec summaries on the two open decisions (2026-06-30)
**Recall semantics:** `sources` conflates two opposing jobs — coverage (full spread of reporting) vs provenance (claim citation). SOTA aggregators (Ground News, Google News "full coverage", Digg's decouple-grouping-from-ranking) show COVERAGE. RECOMMEND: `sources` = coverage / high-recall (it's this product's bias-diversity differentiator); Sonnet 5's 80% is a mild display regression, promptable back up. Keep provenance as a separate internal concern (citation self-check + COHERENCE can use a precise subset). Decouple the two; don't tune one number for both.

**Graph-first:** RECOMMEND pursue, gated by a task-grounded PoC. SOTA converges hard: every serious aggregator does cheap per-article extract (entities+event+time) → deterministic join → thin LLM refine (entity-bag+pub-time = 92 BCubed F1, zero LLM); academic = event/temporal knowledge graphs (event coref → story chains → timeline summ); tooling = GraphRAG / LlamaIndex property-graph. Direction: collapse CLUSTER + thread-linking into "attach articles to the persistent entity/event graph" (late-binding subgraph; extends the threads work down to within-day). Shape: Haiku extract → deterministic join (SQLite+networkx) → thin Sonnet refine → SELECT/WRITE on subgraphs → synthesis walks the graph. KEEP deterministic Python orchestration, small stages, ID-indirection, COHERENCE, eval floor; NO LLM orchestrator; decouple grouping from ranking. WHY NOW: Sonnet 5's cheap-low-effort frontier + cheap Haiku extract make it affordable (the strategic unlock). KEYSTONE RISK/GATE: cheap automated grouping (MiniLM 0.497 ARI) did NOT match Claude's editorial-narrative judgment in our PoC — the cheap-extract→join must clear the narrative bar, validated TASK-GROUNDED (digest quality), never ARI. That PoC is the go/no-go.

## Decisions Sean owes
1. **Recall semantics**: is `sources` for coverage (4.6's 98%) or provenance (Sonnet 5's 80%)? → Phase 3 input.
2. **Phase 1 wrinkle**: (a) inert-by-default lever [recommended] vs (b) activate medium + validate.
3. **Graph redesign (Phase 4)**: pursue, or park and just optimize current shape?

## PoC candidates — evaluate cost-value before adopting (do NOT adopt blind)
Verified this session: LangChain/LlamaIndex do NOT back the Agent SDK as an executor — they call the API (loses subscription billing); industry trend is Agent SDKs *replacing* these frameworks. So none of these touch `claude_cli`/execution. Each is a small PoC with an explicit gate:
1. **Cheap-extract → join → graph clustering** (the keystone Phase-4 bet, from the clustering prior-art): Haiku per-article entity/event/time extraction + deterministic join (SQLite/`networkx`) vs holistic Sonnet CLUSTER. COST: cheap Haiku tokens + build time. VALUE: shrinks Sonnet's input, AND yields the entity/event graph that threading needs. GATE: task-grounded digest quality (Phase 2 eval) ≥ holistic — NOT ARI.
2. **Graph substrate: LlamaIndex PropertyGraphIndex vs SQLite + `networkx`.** Build the persistent story-graph both ways on a snapshot. COST: LlamaIndex dep weight + abstraction tax vs ~0 for the minimal stack. VALUE: does its graph/retrieval machinery earn its complexity at our scale (~470 articles/day)? GATE: adopt only if it beats SQLite+networkx on *capability*, not convenience — minimalism value says default to the small stack.
3. **Observability: Langfuse/LangSmith tracing of Agent SDK runs** (additive, doesn't replace execution). COST: 1 dep + config. VALUE: better trace/usage dashboards + debugging vs the current JSONL→`run_usage` parsing. GATE: low-commitment, reversible — keep if it meaningfully beats current observability.
(Reliability libs — `tenacity`, stdlib `asyncio.timeout` — are NOT PoCs; they're known-good swaps in Phase 5.)

## Phase 5 — Reliability hardening of `claude_cli.py` (separate workstream; audit done 2026-06-30)
SDK = `claude-agent-sdk` 0.2.101. The spawned `claude` CLI already retries transient failures 10× internally, so our outer layers only fire after that or for non-retryable errors. Audit verdict: hand-rolling is *mostly* defensible (covers real SDK gaps — idle watchdog vs synthesis-hang #701, the auth guard, the `api_error_status` reconciliation which is correct SDK usage NOT a bug). Three fragilities to fix, ordered by gain:
1. **[serious] Unguarded generator teardown.** `finally: await agen.aclose()` has no timeout and `run_agent` no outer wall-clock cap → unbounded hang ([SDK #378](https://github.com/anthropics/claude-agent-sdk-python/issues/378)). Fix: `await asyncio.wait_for(agen.aclose(), 5)` (swallow/log TimeoutError). 2 lines, highest gain.
2. **Rate-limit retry is blind.** `retry.py` matches `429`/`rate_limit` into 30–300s backoff, but a subscription/OAuth cap resets hourly (`RateLimitInfo.resets_at` now available in the stream). Sleep-to-reset or fail fast; don't burn the 4h budget polling.
3. **Substring error classification** (`str(err).lower()`) → use structured `api_error_status` (int) + typed exceptions (`ProcessError`, `CLIConnectionError`).
Approach (Sean's calls): use **battle-tested libs** for the harness (`tenacity` for retry/backoff; stdlib `asyncio.timeout` for the idle loop) rather than hand-rolled loops; and ship every genuine SDK-bug workaround (#378 teardown, #701 idle watchdog) with a **canary test** keyed to the SDK version (`xfail(strict=True)` if reproducible, pinned-version assertion otherwise) so it self-expires on the upstream fix — see [[feedback_canary_dep_workarounds]]. NOTE: the `subtype=success`+`is_error` reconciliation is NOT a workaround (documented field) — no canary. Files: `claude_cli.py`, `retry.py` (`_RETRYABLE_PATTERNS`/`is_retryable`), `orchestrate.py:231-319`. **Don't mix this with the Sonnet 5 feature work** — separate session.

## Corrected reasoning (Sean's calls this session, for the record)
- Clustering eval must be **task-grounded**, not ARI/golden (ill-posed; many valid partitions). My "no regression via ARI 0.74" was struck.
- **Keep many-small-prompts** — do NOT collapse CLUSTER+SELECT+WRITE into one agentic loop.
- **No Opus/LLM orchestrator** — Python orchestrates deterministically; that's correct and stays.
