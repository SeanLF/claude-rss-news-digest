"""Claude wrapper backed by the official Claude Agent SDK.

Thin, reusable wrapper around the Claude Agent SDK (`claude-agent-sdk`).
The SDK drives the same `claude` CLI over a subprocess, so it INHERITS the
subscription OAuth / setup-token auth and stays on the Agent-SDK credit. It does
NOT require ANTHROPIC_API_KEY -- setting one would move billing off the
subscription credit to pay-as-you-go, so we deliberately never set it here.

Usage:
    from claude_cli import run_sync, stream_sync   # sync (batch pipelines)
    from claude_cli import run, stream             # async (web servers)

The public signatures match the previous hand-rolled subprocess wrapper so the
rest of the pipeline (claude.py, test_prompt.py) keeps working unchanged. The
streamed events are adapted from the SDK's dataclass message objects into the
same dict shape the old `--output-format stream-json` NDJSON produced, because
claude.py inspects events as dicts (event["type"], event["message"]["content"],
event.get("parent_tool_use_id"), event.get("total_cost_usd"), ...).

Prompts pass straight through to the CLI. The digest stages drive agents by
passing a plain prompt ("Begin.") plus the agent body as the system_prompt (see
orchestrate.py); a prompt beginning with "/" is still interpreted as a slash
command by the CLI if a caller wants that. setting_sources is left as None, so
the SDK loads the same user+project+local settings as the CLI.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfig,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SDK message -> legacy stream-json dict adaptation
# ---------------------------------------------------------------------------
#
# claude.py consumes events shaped like the CLI's `stream-json` NDJSON:
#   {"type": "assistant", "message": {"content": [ {block}, ... ]}, ...}
#   {"type": "user", "parent_tool_use_id": ..., "message": {"content": [...]}}
#   {"type": "result", "subtype": "success", "total_cost_usd": ..., "duration_ms": ...}
# The SDK yields dataclasses instead, so we translate each block/message back
# into that dict form. Keep these in sync with claude.py's expectations.


def _normalize_content(content: Any) -> Any:
    """Normalize a content list of SDK blocks/dicts into stream-json block dicts.

    Non-list content is returned unchanged; list items that are already dicts
    pass through, SDK block dataclasses are converted via ``_block_to_dict``.
    """
    if isinstance(content, list):
        return [b if isinstance(b, dict) else _block_to_dict(b) for b in content]
    return content


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an SDK content block dataclass into a stream-json block dict."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        # Nested content blocks (rare) -> normalize so shape stays consistent.
        content = _normalize_content(block.content)
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": block.is_error,
        }
    # Unknown block type: pass through whatever attributes exist, best-effort.
    return {"type": getattr(block, "type", "unknown")}


def _message_to_event(message: Any) -> dict[str, Any] | None:
    """Adapt an SDK Message dataclass into the legacy stream-json event dict.

    Returns None for message types that have no legacy-event equivalent (e.g.
    partial StreamEvents / rate-limit notices), which callers simply skip.
    """
    if isinstance(message, AssistantMessage):
        return {
            "type": "assistant",
            "parent_tool_use_id": message.parent_tool_use_id,
            "message": {
                "role": "assistant",
                "model": message.model,
                "content": [_block_to_dict(b) for b in message.content],
                "usage": message.usage,
                "id": message.message_id,
            },
        }
    if isinstance(message, UserMessage):
        user_content = _normalize_content(message.content)
        return {
            "type": "user",
            "parent_tool_use_id": message.parent_tool_use_id,
            "message": {"role": "user", "content": user_content},
        }
    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "subtype": message.subtype,
            "is_error": message.is_error,
            "duration_ms": message.duration_ms,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
            "total_cost_usd": message.total_cost_usd,
            "usage": message.usage,
            "result": message.result,
        }
    if isinstance(message, SystemMessage):
        return {"type": "system", "subtype": message.subtype, **(message.data or {})}
    if isinstance(message, StreamEvent):
        # Emitted when include_partial_messages=True (we enable it in
        # _build_options). No legacy equivalent, so skip -- but the raw message
        # still resets the event-idle timer in _stream_events before this filter,
        # which is exactly why we enable partial messages.
        return None
    return None


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
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions from the wrapper's keyword arguments.

    setting_sources is intentionally left at its default (None), which makes the
    SDK load the same user+project+local filesystem settings as the CLI. If a
    future SDK release changes the None default to "load nothing", set
    setting_sources=["user","project","local"].
    """
    # include_partial_messages makes the SDK emit partial-token StreamEvents -- a
    # denser event stream. _message_to_event filters StreamEvents to None so
    # callers are unaffected; the ONLY beneficiary is the event-idle timer in
    # _stream_events, which resets on every message and so catches a mid-
    # generation stall (a model that goes silent between turns) much sooner.
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
        # Legacy API took a comma/space-separated string; SDK wants a list.
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
    return ClaudeAgentOptions(**kwargs)


def _result_text(events: list[dict[str, Any]]) -> str:
    """Reconstruct the plain-text result from collected stream events.

    Mirrors the CLI's default (non-stream) output: the final assistant text. We
    prefer the ResultMessage.result if present, else concatenate assistant text.
    """
    for event in reversed(events):
        if event.get("type") == "result":
            result = event.get("result")
            if result:
                return str(result).strip()
            break
    # Fallback: stitch assistant text blocks together.
    texts: list[str] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
    return "\n".join(texts).strip()


def _check_result(events: list[dict[str, Any]]) -> None:
    """Raise RuntimeError if the run ended without a successful result event.

    The ResultMessage with subtype="success" is the authoritative completion
    signal (same contract as the old NDJSON `result`/`success` event). A budget
    cap trips subtype="error_max_budget_usd"; surface it as a clear error.
    """
    for event in events:
        if event.get("type") == "result":
            subtype = event.get("subtype")
            if subtype == "success":
                return
            raise RuntimeError(f"claude failed: result subtype={subtype!r}")
    raise RuntimeError("claude failed: no result event received")


# ---------------------------------------------------------------------------
# Core async driver
# ---------------------------------------------------------------------------


async def _stream_events(
    prompt: str,
    *,
    model: str,
    system_prompt: str | None,
    permission_mode: str | None,
    allowed_tools: str | None,
    mcp_config: str | Path | None,
    max_turns: int | None,
    max_budget_usd: float | None,
    cwd: str | Path | None,
    idle_timeout: float = 120.0,
    load_timeout_ms: int | None = None,
    tools: list[str] | None = None,
    thinking: ThinkingConfig | None = None,
) -> AsyncGenerator[dict[str, Any]]:
    """Drive the SDK query() and yield adapted legacy-shaped event dicts.

    In-process hang detection: each pull from the SDK iterator is bounded by
    ``idle_timeout`` seconds via ``asyncio.wait_for``. The timer resets on EVERY
    message -- including partial StreamEvents and any other message
    ``_message_to_event`` filters to None -- so a mid-generation stall (the
    subprocess goes silent) is caught, not just a never-started run. On idle we
    raise a RuntimeError whose message contains "idle timeout" (so retry.py
    treats it as retryable) and close the SDK generator to tear down the
    subprocess (no leak).
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
    )
    agen = query(prompt=prompt, options=options).__aiter__()
    try:
        while True:
            try:
                message = await asyncio.wait_for(agen.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except TimeoutError as e:
                raise RuntimeError(f"SDK idle timeout: no event in {idle_timeout}s") from e
            event = _message_to_event(message)
            if event is not None:
                yield event
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()


def _drain_sync(agen: AsyncGenerator[dict[str, Any]]) -> Generator[dict[str, Any]]:
    """Synchronously iterate an async generator, yielding each item.

    Runs a dedicated event loop and pumps the async generator one item at a
    time so the caller sees events as they stream (matching the old behaviour).
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                item = loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
            yield item
    finally:
        loop.run_until_complete(agen.aclose())
        loop.close()


# ---------------------------------------------------------------------------
# Synchronous API
# ---------------------------------------------------------------------------


def run_sync(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    system_prompt: str | None = None,
    output_format: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    json_schema: str | None = None,
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

    `output_format` and `json_schema` are accepted for signature compatibility
    with the old CLI wrapper but are not used by the SDK path (the SDK returns
    structured messages; we reconstruct the text result).
    """
    del output_format, json_schema  # accepted for compat; not used by SDK path

    async def _run() -> str:
        events: list[dict[str, Any]] = []
        agen = _stream_events(
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
        )
        async for event in agen:
            events.append(event)
        _check_result(events)
        return _result_text(events)

    return asyncio.run(asyncio.wait_for(_run(), timeout=timeout))


def stream_sync(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
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
) -> Generator[dict[str, Any]]:
    """Stream a prompt synchronously, yielding legacy-shaped event dicts.

    The ResultMessage with subtype="success" is the authoritative completion
    signal; callers (claude.py) inspect it for cost/duration and to detect
    non-success endings (including the max_budget_usd guardrail tripping).

    ``idle_timeout`` bounds the gap between SDK events (in-process hang
    detection); an idle stall raises a retryable RuntimeError. ``load_timeout_ms``
    bounds subprocess startup (None -> SDK default).
    """
    agen = _stream_events(
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
    )
    yield from _drain_sync(agen)


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def run(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    system_prompt: str | None = None,
    output_format: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    json_schema: str | None = None,
    max_turns: int | None = None,
    max_budget_usd: float | None = None,
    timeout: int = 600,
    cwd: str | Path | None = None,
    idle_timeout: float = 120.0,
    load_timeout_ms: int | None = None,
    tools: list[str] | None = None,
    thinking: ThinkingConfig | None = None,
) -> str:
    """Run a prompt asynchronously and return the full text output."""
    del output_format, json_schema  # accepted for compat; not used by SDK path

    async def _run() -> str:
        events: list[dict[str, Any]] = []
        async for event in _stream_events(
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
        ):
            events.append(event)
        _check_result(events)
        return _result_text(events)

    return await asyncio.wait_for(_run(), timeout=timeout)


async def stream(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
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
) -> AsyncGenerator[dict[str, Any]]:
    """Stream a prompt asynchronously, yielding legacy-shaped event dicts."""
    async for event in _stream_events(
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
    ):
        yield event
