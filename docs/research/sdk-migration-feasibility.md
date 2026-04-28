# SDK Migration Feasibility: `claude --print` to Anthropic Python SDK

**Date**: 2026-04-03  
**Status**: Research only -- no code changed

---

## 1. Current Invocation Mechanism

The pipeline uses a custom `claude_cli.py` wrapper around the `claude` CLI binary. It has two entry points:

**`run_sync(prompt, ...)`** -- wraps `subprocess.run()` around `claude --print --model <model>`. Used for:
- `generate_weekly_recap()`: a single Haiku call to summarise RSS titles
- `health_check()`: verifies auth is working

**`stream_sync(prompt, ...)`** -- wraps `subprocess.Popen` with `--output-format stream-json --verbose`, reading NDJSON line-by-line. Used for:
- `generate_selections()`: the main curation pipeline, driven by the dispatcher prompt

The dispatcher is a Claude Code slash command (`.claude/commands/news-digest-select.md`) that runs in a `claude --print` session under `acceptEdits` permission mode. It orchestrates five subagents via the CLI's built-in `Agent` tool:

1. **CLUSTER** + **RECAP** launched simultaneously (CLUSTER does narrative grouping; RECAP summarises recent RSS titles via Haiku)
2. **SELECT** -- editorial tier assignments and story selection
3. **WRITE** -- headline, summary, why_it_matters copy
4. **COHERENCE** -- Haiku cross-check of headlines vs. source articles

The dispatcher session writes `selections.json` via the `write_selections` MCP tool, which runs as a local Python subprocess (`mcp_server.py`) over stdio.

**Usage tracking** is done entirely outside the invocation path: after the run, `usage.py` parses `~/.claude/projects/.../session.jsonl` files written by the CLI, extracting per-subagent token counts by matching prompt prefixes to dispatch entries in the parent JSONL.

**There is no `anthropic` package in the project's dependencies** (`pyproject.toml` lists feedparser, resend, premailer, jsonschema, yoyo-migrations, beautifulsoup4). The entire Claude surface is the CLI binary.

---

## 2. SDK Capabilities Relevant to This Pipeline

### Messages API
- Standard `client.messages.create(model=..., system=..., messages=..., tools=...)` per call
- Per-call model selection: trivially supported
- System prompt: `system=` parameter
- Tool use: client-defined tools in `tools=[]`; Claude returns `stop_reason: "tool_use"` blocks; caller executes and sends `tool_result` back in a new turn
- `strict: true` on tool definitions guarantees schema conformance (maps to current jsonschema validation in mcp_server.py)
- Token counts returned in every response (`usage.input_tokens`, `usage.output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) -- no JSONL parsing needed
- Streaming: `client.messages.stream()` context manager or `stream=True` on create
- Extended thinking: available as a beta feature

### Batch API
- `client.messages.batches.create(requests=[...])` -- each request is a full Messages API payload with a `custom_id`
- **50% cost discount** on all token types (input, output, cache)
- Most batches complete in under 1 hour; maximum 24 hours; results available 29 days post-creation
- Supports tool use, system prompts, multi-turn conversations, vision, and all beta features
- Up to 100,000 requests or 256 MB per batch
- No streaming within a batch -- results retrieved by polling or webhook
- Not available on Bedrock or Vertex

### MCP Connector
Critical limitation: **the SDK's MCP connector only supports remote HTTP/SSE servers**. Local stdio-based MCP servers (like the current `mcp_server.py` Python subprocess) cannot be connected directly via `mcp_servers=[...]` in the API.

The TypeScript SDK has client-side helpers (`mcpTools`, `StdioClientTransport`) to bridge local stdio MCP servers to the API's tool format, but **these helpers are TypeScript only**. Python has no equivalent.

For Python, connecting to a local stdio MCP server requires either:
1. Implementing the MCP client protocol manually in Python (read stdio, convert to tool definitions, handle tool_use blocks, send back tool_result)
2. Using a third-party Python MCP client library (e.g. `mcp` package from Anthropic/modelcontextprotocol)
3. Exposing the MCP server over HTTP/SSE (adds complexity for a local in-container tool)
4. Dropping MCP entirely and re-implementing `write_selections` as a native SDK `tool_use` definition (simplest path)

---

## 3. Migration Delta

| Component | Current | SDK equivalent | Change complexity |
|---|---|---|---|
| Subagent invocation (simple) | `run_sync()` / `subprocess.run()` | `client.messages.create()` | Low -- 1:1 swap, cleaner error handling |
| Model selection per agent | `--model haiku` CLI flag | `model="claude-haiku-4-5"` param | Low -- requires using full model IDs not aliases |
| System prompt | CLI `--system-prompt` flag or agent .md file | `system=` param | Low -- read the .md file content, pass as string |
| File-based handoff | Read/write JSON files in `data/claude_input/` | Identical | None -- files still work, SDK is agnostic |
| MCP server (`write_selections` tool) | Local stdio MCP subprocess, auto-managed by CLI | Must be reimplemented as SDK `tool_use` definition or HTTP MCP server | **Medium-High** -- see below |
| Token/cost tracking | Parse session JSONL (brittle, internal format) | Native from `response.usage` on every call | Simpler -- eliminates `usage.py` JSONL parser entirely |
| Streaming main dispatcher | CLI streams NDJSON events | `client.messages.stream()` | Low -- SDK streaming is cleaner |
| Multi-turn tool loop | CLI handles the agentic loop internally | Caller manages `messages[]` array, appending `assistant` + `tool_result` turns | **High** -- the main dispatcher's multi-turn agentic loop (subagent dispatch, retry, verification) is currently managed entirely inside the CLI session |
| Subagent parallelism | CLI's `Agent` tool launches subagents concurrently | `asyncio.gather()` on concurrent `client.messages.create()` calls | Medium -- requires asyncio but explicit and testable |
| Batch API | Not available | `client.messages.batches.create()` | New capability -- applicable to selected subagents |
| Error handling | Parse stderr, check exit code | Python exceptions (`anthropic.APIError`, `anthropic.RateLimitError`) | Cleaner -- structured exceptions with retry logic built in |
| Auth health check | `claude --print "respond with ok"` | `client.messages.create(...)` | Trivial |

### The MCP Tool Problem in Detail

The current `mcp_server.py` is a 250-line JSON-RPC/stdio server that receives `write_selections`, validates against a schema, and writes `selections.json`. The CLI manages the entire MCP protocol connection. With the SDK, this becomes a standard `tool_use` definition:

```python
WRITE_SELECTIONS_TOOL = {
    "name": "write_selections",
    "description": "...",
    "input_schema": SELECTIONS_SCHEMA,  # identical to current SOURCE_SCHEMA hierarchy
}
```

When Claude calls the tool, the caller receives `stop_reason: "tool_use"`, executes the validation + file-write logic in Python, and sends a `tool_result` block. The 250-line MCP server is replaced by ~30 lines inlined in the orchestration code. **This is actually simpler**, not harder -- the MCP server exists only because the CLI required it.

### The Dispatcher Loop Problem in Detail

The biggest complexity is the current dispatcher prompt (`news-digest-select.md`) which itself runs as a Claude session using the CLI's `Agent` tool to launch subagents. With the SDK, there is no `Agent` tool. Orchestration moves entirely to Python:

```python
# Current: one CLI session that internally dispatches 5 agents
generate_selections()  # black box, managed by Claude Code's runtime

# SDK equivalent: explicit Python orchestration
cluster_result, recap_result = await asyncio.gather(
    run_cluster_agent(client),
    run_recap_agent(client),
)
selected = await run_select_agent(client)
draft = await run_write_agent(client)
coherence = await run_coherence_agent(client)
selections = assemble_and_filter(draft, coherence)
write_selections_tool(selections)
```

Each subagent call is a single-turn `client.messages.create()` (the subagents do not need multi-turn; they read files and write output). The dispatcher's retry logic (verify output file, retry once on failure) becomes explicit Python. This is more transparent and testable than a Claude prompt controlling other Claude sessions.

---

## 4. Orchestration Model Options

### Option A: Sequential SDK Calls (Minimal Restructure)

Replace each CLI invocation with a `client.messages.create()` call. The Python script drives the same five-stage pipeline explicitly.

- Pro: Straightforward, minimal restructure, easy to reason about
- Pro: Subagent prompts (the .md files defining CLUSTER, SELECT, etc.) can remain; just read them as `system=` or inject into `messages[]`
- Con: Currently CLUSTER + RECAP run in parallel; sequential execution adds ~2-3 min latency
- Con: No cost reduction

### Option B: Asyncio with Concurrent Subagents

Use `asyncio.gather()` for the CLUSTER+RECAP parallel step; sequential `await` for SELECT, WRITE, COHERENCE.

- Pro: Matches current parallelism (CLUSTER+RECAP simultaneous)
- Pro: Explicit, testable, observable
- Pro: Opens door to adding Batch API for independent steps
- Con: Requires converting `run.py` pipeline to async (currently sync); plumbing change across the stack
- Neutral: The file-based handoff still works identically

### Option C: Event-Driven via Asyncio Callbacks

Each subagent completion triggers the next via awaited coroutines. In practice, this collapses to Option B for a five-stage DAG with a single fan-out at step 1. No meaningful architectural difference for this pipeline -- not worth the complexity.

### Option D: Hybrid (Batch API for Eligible Steps)

Submit RECAP (independent, not time-critical) to the Batch API while running CLUSTER synchronously. After CLUSTER completes, run SELECT. After SELECT, run WRITE. COHERENCE could also batch individual article checks.

In practice: RECAP is a single Haiku call taking ~10 seconds. Routing it to a batch that takes up to an hour to get a 50% discount on a $0.01 call is not rational.

**Verdict**: Option B (asyncio with concurrent CLUSTER+RECAP) is the right target. Clean, explicit, testable, preserves parallelism.

---

## 5. Batch API Applicability

The 50% discount is real but the pipeline's architecture limits where it can be applied.

| Subagent | Sequential dependency | Batch-eligible | Realistic saving |
|---|---|---|---|
| CLUSTER | First stage -- no dependency | Technically yes, but blocks SELECT | No -- gating step |
| RECAP | Independent of CLUSTER | Yes | Minimal -- one ~10s Haiku call; discount ~$0.005/day |
| SELECT | Depends on CLUSTER output | No | No |
| WRITE | Depends on SELECT output | No | No |
| COHERENCE | Depends on WRITE output; individual story checks are independent | Partial -- could batch per-story checks | Moderate -- but adds up-to-1h wait at the end of the pipeline |

**The pipeline is a sequential DAG**. Each stage depends on the previous stage's file output. The Batch API's asynchronous nature (up to 1 hour wait) is incompatible with a digest that needs to complete within a reasonable run window (currently ~10-15 min total).

The only genuinely batch-eligible calls are:
1. `generate_weekly_recap()` -- currently a Haiku call. At $0.01/run, 50% = $0.005. Not worth the architectural complexity of polling for a batch result.
2. Coherence checks, if broken into one API call per story (~10-15 calls). These are currently run as a single Haiku session reading all drafts. Could theoretically batch them, but adds up-to-1h latency at the worst time (end of pipeline, just before email send).

**Conclusion**: The Batch API does not fit this pipeline's real-time constraints. The cost savings would be marginal given the pipeline's sequential DAG and the fact that the expensive step (CLUSTER via Sonnet -- ~63% of cost, ~$6-7/day) is a gating dependency for everything downstream.

---

## 6. Effort Estimate and Biggest Risks

### Effort

| Task | Lines changed (est.) | Complexity |
|---|---|---|
| Add `anthropic` to pyproject.toml | 1 | Trivial |
| Replace `run_sync()` with `client.messages.create()` for health check + weekly recap | ~30 | Low |
| Replace `stream_sync()` + dispatcher prompt with explicit Python orchestration | ~150 | Medium |
| Inline `write_selections` as SDK tool_use (replace mcp_server.py) | -220 (delete), +30 | Medium |
| Replace JSONL-based usage tracking with `response.usage` fields | -200 (delete usage.py), +40 | Medium |
| Convert pipeline to asyncio for CLUSTER+RECAP parallelism | ~80 across run.py + claude.py | Medium |
| Update tests | ~50 | Low |
| **Total** | **~280 net new, ~420 deleted** | **Medium overall** |

Elapsed wall-clock time for an experienced developer comfortable with the codebase: 1-2 days of focused work.

### Biggest Risks

**1. Losing the `Agent` tool and subagent isolation**

The current model uses the CLI's built-in `Agent` tool, which runs each subagent in a fully isolated session with its own context window. With the SDK, each `messages.create()` call is a fresh context -- which is actually fine for this pipeline since the subagents are single-turn. But any assumption that subagents inherit parent state (or share tool access) would break.

**2. Prompt compatibility -- subagent .md files reference Claude Code tools**

The five subagent prompts (CLUSTER, RECAP, SELECT, WRITE, COHERENCE) are written as Claude Code agent prompts. They use Read and Write tool instructions ("Use the Read tool to read...", "Use the Write tool to write..."). With the SDK, these tools don't exist -- the subagent is just a text-in, text-out call that reads files via... the same `acceptEdits` permission mode? No: the SDK does not have an `acceptEdits` permission mode. The subagents would need to be redesigned to either (a) receive file contents as message content and return structured JSON directly, or (b) use explicit file-read tool definitions passed to the SDK call.

This is the **most underestimated rewrite**. The subagent prompts were written assuming Claude Code's tool environment. SDK subagents would need the article data injected into the prompt/message content rather than read via file tools. For CLUSTER, this means injecting the full articles CSV content (potentially 50-100k tokens) into the message. That changes caching dynamics significantly.

**3. Prompt caching dynamics change**

Currently the dispatcher + subagents benefit from the CLI's automatic prompt caching (cache breakpoints are set on system prompts). With the SDK, explicit cache control headers (`cache_control: {"type": "ephemeral"}`) must be set on the appropriate message blocks. Getting this wrong loses the 63% cache-read discount that makes CLUSTER affordable.

**4. Usage tracking becomes trivial -- but the `run_usage` table schema and UI depend on per-subagent rows**

The current `run_usage` table stores one row per subagent per model. With the SDK, `response.usage` is per-API-call. This maps well as long as each subagent is one call. If a subagent uses multi-turn tool loops, usage aggregation must be done manually. The web UI (circulation) shows this table -- no schema change needed, just the recording logic.

**5. No model aliases -- must use full model IDs**

`claude --model haiku` maps to whatever Haiku version Claude Code uses. The SDK requires explicit model IDs like `claude-haiku-4-5`. These must be kept up to date, and the `MODEL_NAME` env var + pricing table in `usage.py` would need rethinking.

---

## 7. Recommendation

**Defer. Not "not worth it," but not now.**

### Rationale

The primary gains from migrating are:
1. Simpler usage tracking (eliminate JSONL parsing)
2. Cleaner error handling
3. Native async/await orchestration
4. Potential Batch API access

Against those:
- **Batch API saves nothing meaningful** for this pipeline. The sequential DAG and real-time constraints make it inapplicable to the expensive steps. RECAP is the only clean candidate and it's a rounding error in the cost profile.
- **The subagent prompt rewrite is the real cost**. The five subagent prompts are tightly coupled to Claude Code's tool environment (Read/Write file tools, acceptEdits permission mode). Migrating to the SDK requires redesigning how article data reaches the subagents -- probably injecting CSV content into the message itself. This changes prompt structure, caching strategy, and potentially output quality (more context = more distraction, less cache efficiency).
- **The current system works and is observable**. The CLI wrapper is thin (270 lines), the stream event parsing is minimal, and the JSONL usage tracker, while brittle, has been stable.
- **The `write_selections` MCP-to-tool-use rewrite is actually a win**, but it's a small, self-contained change that could be done independently of a full SDK migration.

### When to revisit

Migrate if any of these become true:
- Claude Code's CLI is deprecated or the JSONL session log format changes (making usage tracking break)
- A pipeline stage becomes genuinely parallelisable and async orchestration would meaningfully reduce runtime
- The Batch API becomes applicable (e.g. a new "pre-filter" stage that processes hundreds of independent article summaries before the main curation)
- The project moves to direct API billing (making the 50% Batch discount worth chasing)

### Incremental steps worth taking now (without full migration)

1. **Inline `write_selections` as SDK tool_use** -- delete `mcp_server.py`, add `anthropic` as a dependency, implement the tool call natively. This is a clean win: -220 lines of JSON-RPC server, +30 lines of tool definition and handler. No impact on the rest of the pipeline. The MCP server exists only because the CLI required it; there is no reason to keep it if the architecture changes even slightly.

2. **Add `ANTHROPIC_API_KEY` env var to the container** -- required for SDK usage; harmless now, enables future calls.

These two steps move the project toward SDK readiness without committing to the full orchestration rewrite.
