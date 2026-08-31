"""Claude wrapper backed by the official Claude Agent SDK.

Thin, reusable wrapper around the Claude Agent SDK (`claude-agent-sdk`).
The SDK drives the same `claude` CLI over a subprocess, so it INHERITS the
subscription OAuth / setup-token auth and stays on the Agent-SDK credit. It does
NOT require ANTHROPIC_API_KEY -- setting one would move billing off the
subscription credit to pay-as-you-go, so we deliberately never set it here.

Two entry points, both driven by the same async SDK loop:

    run_sync(prompt, ...) -> str        # sync text callers (recap, health check, eval judge)
    run_agent(prompt, ...) -> StageResult  # async core, awaited by the curation orchestrator

The orchestrator awaits ``run_agent`` directly so the whole curation phase runs
under one event loop (orchestrate.py opens it once via ``asyncio.run``); the only
sync bridge is ``run_sync`` for the one-shot text callers.

We consume the SDK's dataclass messages directly -- the previous NDJSON adapter
that reshaped them back into the old `--output-format stream-json` dict shape is
gone (that wire format belonged to a transport we no longer use). The only thing
any caller needs from a finished run is the terminal :class:`ResultMessage`,
distilled into a small :class:`StageResult`; text callers just read its ``text``.

Prompts pass straight through to the CLI. The digest stages drive agents by
passing a plain prompt ("Begin.") plus the agent body as the system_prompt (see
orchestrate.py). setting_sources is left as None, so the SDK loads the same
user+project+local settings as the CLI.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfig,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from config import DEFAULT_MODEL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageResult:
    """Distilled outcome of one completed agent run.

    Built from the SDK's terminal :class:`ResultMessage`; this is everything any
    caller needs. ``text`` is the final result text (the ResultMessage ``result``
    field, else the concatenated assistant text). ``usage`` is the SDK token-usage
    dict that ``usage.usage_row_from_sdk`` turns into a ``run_usage`` row.
    """

    subtype: str | None
    text: str
    usage: dict[str, Any]
    total_cost_usd: float
    duration_ms: int
    # The SDK can report a FAILED upstream API call while still tagging the run
    # subtype="success": is_error flips True and api_error_status carries the HTTP
    # code (429/500/529...). Captured so ``ok`` can reject it and callers can raise
    # a retryable, status-bearing error. Default to the clean-success shape.
    is_error: bool = False
    api_error_status: int | None = None
    # Paths the agent actually opened with the Read tool, first-seen order. Every
    # stage is HANDED input files; nothing proved it read them. Without this,
    # "read it and ignored it" and "never opened it" are indistinguishable from
    # the outside -- which is exactly the ambiguity that sat under the measured-null
    # WRITE experiment. Empty is honest for stages that read nothing.
    files_read: tuple[str, ...] = ()
    # Summarized reasoning, when the stage asked for `display: summarized`. Empty when
    # display is omitted (the Sonnet 5 default) -- which is not "the model did not think",
    # only "the thinking was not sent". Billed identically either way.
    thinking: str = ""

    @property
    def ok(self) -> bool:
        """True only on a genuinely successful run.

        Two ways to be not-ok: a non-success subtype (e.g. the budget cap trips
        subtype="error_max_budget_usd"), OR the SDK's trap shape -- subtype="success"
        but is_error=True (an API call failed; api_error_status holds the code). The
        old check looked only at subtype and shipped that broken run as success.
        """
        return self.subtype == "success" and not self.is_error

    def error_summary(self) -> str:
        """Human-/retry-readable failure detail for a not-ok result.

        Includes ``api_error_status`` when set so the code (529/429/500...) lands in
        the raised error message -- that is what ``retry.is_retryable`` matches to
        treat a transient API failure as retryable rather than a hard stage failure.
        """
        parts = [f"subtype={self.subtype!r}"]
        if self.api_error_status is not None:
            parts.append(f"api_error_status={self.api_error_status}")
        return " ".join(parts)


def _build_options(
    *,
    model: str,
    system_prompt: str | None,
    permission_mode: str | None,
    allowed_tools: str | None,
    mcp_config: str | Path | None,
    max_turns: int | None,
    max_budget_usd: float | None,
    cwd: str | Path | None,
    load_timeout_ms: int | None = None,
    tools: list[str] | None = None,
    thinking: ThinkingConfig | None = None,
    effort: str | None = None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions from the wrapper's keyword arguments.

    setting_sources is intentionally left at its default (None), which makes the
    SDK load the same user+project+local filesystem settings as the CLI. If a
    future SDK release changes the None default to "load nothing", set
    setting_sources=["user","project","local"].

    We never populate ``env`` with credentials: the SDK inherits the process
    environment (subscription OAuth), and injecting ANTHROPIC_API_KEY would move
    billing to pay-as-you-go.
    """
    # include_partial_messages makes the SDK emit partial-token StreamEvents -- a
    # denser event stream. The driver ignores their content; the ONLY beneficiary
    # is the event-idle watchdog in run_agent, which resets on every
    # message and so catches a mid-generation stall (a model that goes silent
    # between turns) much sooner.
    kwargs: dict[str, Any] = {"model": model, "include_partial_messages": True}
    if load_timeout_ms is not None:
        # Startup-hang detection: cap how long the SDK waits for the subprocess
        # to come up before failing (None -> SDK default).
        kwargs["load_timeout_ms"] = load_timeout_ms
    if system_prompt is not None:
        kwargs["system_prompt"] = system_prompt
    if permission_mode is not None:
        kwargs["permission_mode"] = permission_mode
    if allowed_tools is not None:
        # Callers pass a comma/space-separated string; the SDK wants a list.
        kwargs["allowed_tools"] = [t for t in allowed_tools.replace(",", " ").split() if t]
    if tools is not None:
        # Availability restriction (which tools EXIST), distinct from allowed_tools
        # (auto-approval). See orchestrate._tool_list for why the stages need this.
        kwargs["tools"] = tools
    if mcp_config is not None:
        kwargs["mcp_servers"] = str(mcp_config)
    if max_turns is not None:
        kwargs["max_turns"] = max_turns
    if max_budget_usd is not None:
        # $100-credit guardrail: the SDK stops the run client-side once its cost
        # estimate reaches this value, surfacing subtype="error_max_budget_usd".
        kwargs["max_budget_usd"] = max_budget_usd
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if thinking is not None:
        # Match the old Task-subagent behaviour: subagents ran with extended
        # thinking OFF, but a top-level SDK query() defaults it ON for Sonnet.
        # That regression made the CLUSTER stage burn its whole output budget
        # reasoning over ~460 articles and trip the 32k output-token ceiling.
        # Callers pass {"type": "disabled"} to restore the proven-good behaviour.
        kwargs["thinking"] = thinking
    if effort is not None:
        # Output-level reasoning/spend dial (low|medium|high|max). Opt-in per
        # stage via `.claude/agents/*.md` frontmatter. NOT set by default: Haiku
        # 4.5 used to 400 on effort (no longer reproduces on SDK 0.2.110 per
        # bin/sdk-canary), and RECAP/Haiku has no reason to spend on it -- so it
        # stays absent unless a stage opts in.
        kwargs["effort"] = effort
    return ClaudeAgentOptions(**kwargs)


# ---------------------------------------------------------------------------
# Core async driver
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    max_turns: int | None = None,
    max_budget_usd: float | None = None,
    cwd: str | Path | None = None,
    idle_timeout: float = 120.0,
    load_timeout_ms: int | None = None,
    tools: list[str] | None = None,
    thinking: ThinkingConfig | None = None,
    effort: str | None = None,
) -> StageResult:
    """Drive the SDK query() to completion and return a :class:`StageResult`.

    The async core, awaited by the curation orchestrator under its single event
    loop. It does NOT raise on a non-success subtype (e.g. the max_budget_usd
    guardrail) -- the caller inspects ``.ok`` and decides, so it can attach a
    stage-labelled error. There is no overall wall-clock timeout here:
    ``idle_timeout`` bounds the gap between events and the orchestrator's retry
    budget bounds the total.

    In-process hang detection: each pull from the SDK iterator is bounded by
    ``idle_timeout`` seconds via ``asyncio.wait_for``. The timer resets on EVERY
    message -- including the content-free partial StreamEvents -- so a
    mid-generation stall (the subprocess goes silent) is caught, not just a
    never-started run. On idle we raise a RuntimeError whose message contains
    "idle timeout" (so retry.py treats it as retryable) and close the SDK
    generator to tear down the subprocess (no leak).

    Raises RuntimeError if the stream ends without a terminal ResultMessage.
    """
    options = _build_options(
        model=model,
        system_prompt=system_prompt,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        mcp_config=mcp_config,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        cwd=cwd,
        load_timeout_ms=load_timeout_ms,
        tools=tools,
        thinking=thinking,
        effort=effort,
    )
    text_parts: list[str] = []
    files_read: list[str] = []
    thinking_parts: list[str] = []
    # tool_use_id -> path, for Reads awaiting their result.
    pending_reads: dict[str, str] = {}
    result: ResultMessage | None = None
    agen = query(prompt=prompt, options=options).__aiter__()
    try:
        while True:
            try:
                message = await asyncio.wait_for(agen.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except TimeoutError as e:
                raise RuntimeError(f"SDK idle timeout: no event in {idle_timeout}s") from e
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        # Collect assistant text as the fallback for ResultMessage.result.
                        text_parts.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        thinking_parts.append(block.thinking)
                    elif isinstance(block, ToolUseBlock) and block.name == "Read":
                        # Only the model ASKING to open a file. Held until its result
                        # arrives below -- see files_read. isinstance guards a malformed
                        # tool call: input is dict[str, Any], and a non-str path would
                        # later blow up Path() in the caller's log line, killing a run
                        # that had already succeeded and paid for itself.
                        path = (block.input or {}).get("file_path")
                        if isinstance(path, str) and path:
                            pending_reads[block.id] = path
            elif isinstance(message, UserMessage):
                # The tool_result answering a Read. An agent told to open a file "if it
                # exists" still emits the tool_use when it does not, and gets an ERROR
                # back -- so only a non-error result proves the file was opened.
                # content is str | list; only the list form carries blocks.
                for block in message.content if isinstance(message.content, list) else ():
                    if isinstance(block, ToolResultBlock):
                        path = pending_reads.pop(block.tool_use_id, None)
                        # Deduped, first-seen: agents re-read a file across turns (paging a
                        # long input), and one file paged 40 times would bury every other entry.
                        if path and not block.is_error and path not in files_read:
                            files_read.append(path)
            elif isinstance(message, ResultMessage):
                result = message
            # Any other message (SystemMessage, partial StreamEvent) carries nothing a
            # caller needs -- but each one still reset the idle timer above, which is
            # the whole point of streaming them.
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            # Best-effort bound on teardown. The idle watchdog above only guards
            # __anext__ and run_agent has no outer wall-clock cap, so an unguarded
            # teardown stall would otherwise be unbounded. Caveat: asyncio.wait_for
            # only fires if aclose() yields to the loop AND honors cancellation -- it
            # CANNOT interrupt a CPU-bound busy-spin (the "100% CPU" half of SDK #378).
            # So this catches async-stall teardowns, not a non-yielding hang; kept
            # regardless as sound hygiene (inert when aclose() is well-behaved). If
            # #378 turns out to be a pure CPU spin, the real fix is teardown in a
            # thread/subprocess with a hard kill -- follow-up, not done here.
            try:
                await asyncio.wait_for(aclose(), timeout=5)
            except TimeoutError:
                logger.warning("SDK generator aclose() exceeded 5s (SDK #378); abandoning teardown")

    if result is None:
        raise RuntimeError("claude failed: no result event received")
    text = (result.result or "\n".join(text_parts)).strip()
    return StageResult(
        subtype=result.subtype,
        text=text,
        usage=result.usage or {},
        total_cost_usd=result.total_cost_usd or 0.0,
        duration_ms=result.duration_ms or 0,
        is_error=bool(result.is_error),
        api_error_status=result.api_error_status,
        files_read=tuple(files_read),
        thinking="\n".join(thinking_parts),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_sync(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    max_turns: int | None = None,
    max_budget_usd: float | None = None,
    timeout: int = 30,
    cwd: str | Path | None = None,
    idle_timeout: float = 120.0,
    load_timeout_ms: int | None = None,
    tools: list[str] | None = None,
    thinking: ThinkingConfig | None = None,
) -> str:
    """Run a prompt synchronously and return the full text output.

    For text callers (the weekly recap, the auth health check, the eval judge).
    Raises RuntimeError if the run does not end successfully. ``timeout`` is an
    overall wall-clock cap on the whole call; ``idle_timeout`` is the per-event
    hang watchdog inside it.
    """
    result = asyncio.run(
        asyncio.wait_for(
            run_agent(
                prompt,
                model=model,
                system_prompt=system_prompt,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                mcp_config=mcp_config,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                cwd=cwd,
                idle_timeout=idle_timeout,
                load_timeout_ms=load_timeout_ms,
                tools=tools,
                thinking=thinking,
            ),
            timeout=timeout,
        )
    )
    if not result.ok:
        raise RuntimeError(f"claude failed: {result.error_summary()}")
    return result.text
