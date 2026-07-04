# Claude Agent SDK — standards reference

> As of 2026-07, verify before relying: model IDs, pricing, package names, and beta/GA status drift.
> Check `docs.anthropic.com` / `platform.claude.com` (Agent SDK docs + changelog) and the SDK CHANGELOGs before quoting specifics. Facts below were verified against official docs on 2026-07-04.

Scope note for this repo: `newsroom/src/orchestrate.py` drives Claude Code **headless** (`claude --print` via `claude_cli.py`, from Docker), reading `.claude/agents/*.md` as subagent definitions. That is the CLI surface of the *same* agent loop the Agent SDK exposes as a library — so the SDK's concepts (subagents, permission modes, MCP, hooks, sessions) map 1:1 to how this project already works.

## 1. What the Agent SDK is (mid-2026)
- "Claude Code as a library": the same agent loop, built-in tools, context management, and permission system that power Claude Code, driven from your own program.
- Two first-party packages: TypeScript `@anthropic-ai/claude-agent-sdk` (npm; bundles a native Claude Code binary as an optional dep — no separate install), Python `claude-agent-sdk` (pip; requires Python 3.10+).
- Entry point is `query(prompt, options)` — an async iterator over messages. Built-in tools ship ready: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Monitor, AskUserQuestion.
- Headless CLI equivalent = `claude --print` (what this repo uses). CLI is for interactive/one-off; SDK for CI/CD, custom apps, production automation. Workflows translate directly between them.
- Not the same as **Managed Agents** (hosted REST API where Anthropic runs the loop + a per-session sandbox) or the **Client SDK** (`anthropic` / `@anthropic-ai/sdk` — raw Messages API, you write the tool loop). Agent SDK runs the loop in *your* process on *your* filesystem; session state is JSONL on your disk.
- MCP integration: pass `mcp_servers` / `mcpServers` (stdio or URL) to attach external tools without hand-writing tool code.
- Permission modes (option `permission_mode` / `permissionMode`): `default`, `acceptEdits` (this repo's mode), `plan`, `bypassPermissions`, plus `dontAsk`. Fine-grained control via `allowed_tools`/`allowedTools`, `disallowed_tools`, and a runtime `canUseTool` callback.
- Auth: `ANTHROPIC_API_KEY` by default; third-party providers via env flags (`CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`, `CLAUDE_CODE_USE_FOUNDRY=1`, `CLAUDE_CODE_USE_ANTHROPIC_AWS=1`). claude.ai login is not permitted for third-party products — use API keys.

## 2. Model selection (verified against models overview, 2026-07-04)
Current lineup and Claude API IDs / pricing ($/MTok in / out), all 1M context except Haiku:
- `claude-fable-5` — most capable widely released; long-horizon agents. $10 / $50. Thinking always on. Requires 30-day retention (no ZDR). Use only when explicitly chosen — above Opus pricing.
- `claude-opus-4-8` — default for complex agentic coding / enterprise. $5 / $25. Adaptive thinking; `effort` defaults to `high`.
- `claude-sonnet-5` — best speed/intelligence balance; near-Opus on coding/agentic. $3 / $15 ($2 / $10 intro through 2026-08-31). Adaptive thinking on by default.
- `claude-haiku-4-5` (`-20251001`) — fastest, near-frontier for simple work. $1 / $5. 200K context, 64K max out.
- Routing heuristic (matches this repo): cheap/mechanical stages (RECAP summarization) → Haiku; editorial-judgment + faithfulness stages (CLUSTER, SELECT, WRITE, COHERENCE) → Sonnet 5 or Opus 4.8. Reserve Opus 4.8 for stages where a quality miss is expensive; Fable 5 only for genuinely hard long-horizon reasoning. Never a cheap model at high effort or an expensive one on rote work.
- `effort` (low/medium/high/xhigh/max) is the primary intelligence↔cost lever on 4.7+; combine with adaptive thinking.

## 3. Agentic-pipeline best practices (this repo already does most of these)
- **Deterministic orchestration over LLM dispatch**: Python fixes the stage order and runs each subagent as a discrete step, rather than one LLM deciding what to call. Reproducible, debuggable, cheaper — keep it.
- **File-based handoff to bound parent context**: subagents read/write JSON files; the parent stays ~40k tokens. This is the SDK's own recommendation for keeping context small (subagents get their own context window).
- **Least-privilege tool scoping**: give each subagent only the tools it needs (this repo: Read/Write only, `acceptEdits`). Subagents *cannot* raise interactive permission prompts — a tool that hits an `ask` rule inside a subagent is treated as **denied**, so pre-approve via `allowed_tools`/`allowedTools` or it silently fails.
- **Structured output / schema forcing**: validate assembled output against a JSON schema (this repo: `schema.SELECTIONS_SCHEMA` in `merge.py`). For direct API calls prefer `output_config.format` (JSON schema) or strict tool use over prompt-only formatting.
- **Prompt caching**: caching is a prefix match — keep the stable prefix (frozen system prompt, deterministic/sorted tool list) first, volatile content (per-run titles, timestamps) last. A single byte change in the prefix invalidates everything after it. Verify with `usage.cache_read_input_tokens`.
- **Subagent isolation**: separate context per subagent limits blast radius and keeps each prompt focused; the parent only sees returned files/results.

## 4. Cost & observability
- **`total_cost_usd` is the source of truth** for per-stage cost — this repo already passes the SDK's reported cost through from `orchestrate.py` into `usage.py`. Do NOT re-add a hand-rolled pricing table (it goes stale; removed since `3690210`).
- Prompt caching is the biggest lever: cache reads ~0.1x input price, writes ~1.25x (5m TTL) / 2x (1h). Break-even ~2 requests at 5m TTL.
- Token budgeting: count with the `count_tokens` endpoint (model-specific — never `tiktoken`); set `max_tokens` with headroom (truncation forces a retry). Fable 5 / Opus 4.8 / Sonnet 5 share the Opus-4.7 tokenizer (~30% more tokens vs pre-4.7 for the same text) — re-baseline budgets on any model swap.
- Batching: for non-latency-sensitive fan-out, the Message Batches API is 50% off (not an Agent-SDK feature, but relevant if a stage is refactored to direct API calls).
- Session state is JSONL on disk (`~/.claude/...`); this repo already parses it for per-subagent token counts. `CLAUDE_CODE_EAGER_FLUSH=1` forces synchronous writes (required for that tracking).

## 5. Reliability
- The underlying Client SDKs auto-retry 408/409/429/5xx with exponential backoff (default 2 retries); configure via `max_retries`. Prefer battle-tested reliability libs (tenacity, `asyncio.timeout`) over hand-rolled loops.
- Timeouts: default 10 min; stream (`.get_final_message()` / `.finalMessage()`) for large `max_tokens` to dodge idle-connection drops.
- **The SDK floats unpinned in this repo** (prod == CI == PyPI-latest). That is a supply-chain + behaviour-drift risk. Mitigation = canary-per-version: on every bump, run a canary that exercises one full pipeline and diffs output before trusting prod. Pin (or lockfile-freeze) if a bump regresses.
- Ship a self-expiring canary for any SDK/CLI bug workaround (xfail-strict test or version guard) so it fails loudly when upstream fixes it.

## 6. Direction / trajectory (2026 — verify against changelog; flag stable vs emerging)
- **Stable / GA in the SDK today**: subagents (`agents` / `AgentDefinition`), hooks (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, …), sessions (resume + fork), MCP, `.claude/`-based config loading (`setting_sources`/`settingSources`), plugins.
- **Agent Skills** (`SKILL.md`, progressive disclosure, auto-invoked or `/name`) are the current direction for packaging reusable capability — replacing the legacy `.claude/commands/*.md` slash-command format for new work. Loaded from `.claude/skills/*/SKILL.md`.
- **Memory** = `CLAUDE.md` / `.claude/CLAUDE.md` project context today; richer cross-session memory is maturing on the Managed Agents side (memory stores) and via the client-side memory tool.
- **MCP maturation**: the standard tool-connection layer; hundreds of servers; auth for hosted servers is moving to vault/OAuth patterns on the managed side.
- **Managed Agents** (hosted loop + per-session sandbox, deployments/cron, multi-agent coordinator threads) is the emerging path for production/async agents — the documented migration is "prototype with Agent SDK locally → Managed Agents for production." Still beta.
- **Computer use** remains beta across surfaces. High-resolution vision (up to 2576px long edge) is GA on 4.7+ and Sonnet 5.
- Treat everything under Managed Agents, computer use, and cross-session memory as **emerging/beta**; the local Agent SDK loop (query + subagents + hooks + MCP + sessions) is **stable**.

## 7. Common pitfalls
- **`claude -p` cannot run nested inside a Claude Code session** — `CLAUDECODE=1` blocks it; unsetting produces empty/flaky output. This repo avoids it by running `claude --print` from Docker (subprocess), not from inside a Claude Code session. Don't try to invoke it from within one.
- **Unpinned-SDK supply-chain risk** (see §5): prod floating to PyPI-latest means an upstream release can change behaviour or introduce a compromised dep with no gate. Canary every version.
- **Context bloat**: putting work in the parent instead of file-based subagent handoff blows past the ~40k budget and inflates cost. Keep the parent thin.
- **Over-permissioning**: broad `allowed_tools` or `bypassPermissions` in automation removes the safety net. Scope per subagent; remember subagents can't prompt, so an unlisted tool that hits an `ask` rule is denied, not queued.
- **Subagent file reversion**: subagents writing under `acceptEdits` can silently restore stale file versions from git history — verify intermediate JSON content when output looks wrong.
- **Stale model/pricing from memory**: never assert an ID or price from recall; the lineup shifts (Fable 5 GA'd 2026-06-09). Re-verify against the models overview.
