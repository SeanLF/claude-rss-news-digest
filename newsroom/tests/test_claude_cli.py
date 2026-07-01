"""Tests for claude_cli.py: the plain Agent-SDK wrapper.

These mock the Claude Agent SDK's query() so they never spend credit. After the
NDJSON-adapter removal, the wrapper consumes SDK dataclasses directly (no more
reshaping into the old subprocess stream-json dict shape) and exposes two entry
points:

    run_sync(...) -> str            # text callers (recap, health check, eval judge)
    run_agent(...) -> StageResult   # async core, awaited by the curation orchestrator

Both are driven by an in-process idle watchdog (asyncio.wait_for per SDK pull).
Two invariants this module MUST preserve, each guarded below:
  * subscription OAuth -- never set ANTHROPIC_API_KEY (that would move billing
    off the subscription credit to pay-as-you-go);
  * the event-idle watchdog that raises on a wedged call.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import claude_cli
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)


def _result(
    *,
    subtype="success",
    usage=None,
    total_cost_usd=0.0,
    duration_ms=10,
    result=None,
    is_error=None,
    api_error_status=None,
):
    """Build an SDK ResultMessage like query() yields as the terminal event.

    ``is_error`` defaults to ``subtype != "success"`` but can be set explicitly to
    model the SDK's nastiest shape: subtype="success" yet is_error=True with an
    ``api_error_status`` (an HTTP API failure the CLI still reports as a "success"
    subtype). See ResultMessage docs for api_error_status.
    """
    return ResultMessage(
        subtype=subtype,
        duration_ms=duration_ms,
        duration_api_ms=duration_ms,
        is_error=(subtype != "success") if is_error is None else is_error,
        num_turns=1,
        session_id="s1",
        total_cost_usd=total_cost_usd,
        usage=usage,
        result=result,
        api_error_status=api_error_status,
    )


def _fake_query(messages):
    """Return an async-generator factory yielding the given SDK messages."""

    async def _gen(*, prompt, options, transport=None):
        for m in messages:
            yield m

    return _gen


def _run_agent(*a, **k):
    """Drive the async ``run_agent`` to completion from a sync test body.

    The orchestrator awaits ``run_agent`` under one event loop; the tests pump it
    with asyncio.run so we don't need pytest-asyncio.
    """
    return asyncio.run(claude_cli.run_agent(*a, **k))


# ---------------------------------------------------------------------------
# StageResult: the small domain type distilled from the SDK ResultMessage
# ---------------------------------------------------------------------------


class TestStageResult:
    def test_ok_true_on_success(self):
        r = claude_cli.StageResult(subtype="success", text="x", usage={}, total_cost_usd=0.0, duration_ms=1)
        assert r.ok is True

    def test_ok_false_on_budget_cap(self):
        r = claude_cli.StageResult(subtype="error_max_budget_usd", text="", usage={}, total_cost_usd=0.0, duration_ms=1)
        assert r.ok is False

    def test_ok_false_on_missing_subtype(self):
        r = claude_cli.StageResult(subtype=None, text="", usage={}, total_cost_usd=0.0, duration_ms=1)
        assert r.ok is False

    def test_ok_false_when_is_error_despite_success_subtype(self):
        # The SDK's trap: a failed upstream API call can still report
        # subtype="success" while is_error=True (api_error_status set). The old
        # ".ok = subtype == success" read this as success and shipped a broken run.
        r = claude_cli.StageResult(
            subtype="success",
            text="",
            usage={},
            total_cost_usd=0.0,
            duration_ms=1,
            is_error=True,
            api_error_status=529,
        )
        assert r.ok is False

    def test_ok_true_defaults_is_error_false(self):
        # Back-compat: callers that don't pass is_error get a clean success.
        r = claude_cli.StageResult(subtype="success", text="x", usage={}, total_cost_usd=0.0, duration_ms=1)
        assert r.ok is True
        assert r.is_error is False
        assert r.api_error_status is None


# ---------------------------------------------------------------------------
# run_agent: the orchestrator path -> StageResult (async core)
# ---------------------------------------------------------------------------


class TestRunAgent:
    def test_distills_result_message(self, monkeypatch):
        usage = {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 8000,
        }
        messages = [
            AssistantMessage(
                content=[ToolUseBlock(id="tu_1", name="Read", input={"file_path": "x"})],
                model="claude-sonnet",
                parent_tool_use_id=None,
            ),
            _result(subtype="success", usage=usage, total_cost_usd=1.23, duration_ms=2000),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        res = _run_agent("Begin.", model="sonnet", permission_mode="acceptEdits")

        assert isinstance(res, claude_cli.StageResult)
        assert res.ok is True
        assert res.subtype == "success"
        assert res.usage == usage
        assert res.total_cost_usd == 1.23
        assert res.duration_ms == 2000

    def test_non_success_returns_not_ok_without_raising(self, monkeypatch):
        # The orchestrator inspects .ok and raises its own labelled error, so the
        # wrapper itself must NOT raise on a non-success subtype (e.g. budget cap).
        monkeypatch.setattr(claude_cli, "query", _fake_query([_result(subtype="error_max_budget_usd")]))
        res = _run_agent("Begin.", model="sonnet")
        assert res.ok is False
        assert res.subtype == "error_max_budget_usd"

    def test_distills_api_error_despite_success_subtype(self, monkeypatch):
        # subtype="success" but is_error=True with an HTTP status: must surface as
        # not-ok and carry the status so the orchestrator can raise a retryable error.
        messages = [_result(subtype="success", is_error=True, api_error_status=529)]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))
        res = _run_agent("Begin.", model="sonnet")
        assert res.ok is False
        assert res.is_error is True
        assert res.api_error_status == 529

    def test_missing_usage_becomes_empty_dict(self, monkeypatch):
        monkeypatch.setattr(claude_cli, "query", _fake_query([_result(usage=None)]))
        res = _run_agent("Begin.", model="sonnet")
        assert res.usage == {}

    def test_no_result_message_raises(self, monkeypatch):
        msgs = [AssistantMessage(content=[TextBlock(text="hi")], model="m", parent_tool_use_id=None)]
        monkeypatch.setattr(claude_cli, "query", _fake_query(msgs))
        with pytest.raises(RuntimeError, match="no result"):
            _run_agent("Begin.", model="sonnet")


# ---------------------------------------------------------------------------
# run_sync: the text path -> str
# ---------------------------------------------------------------------------


class TestRunSync:
    def test_returns_result_text(self, monkeypatch):
        messages = [
            AssistantMessage(content=[TextBlock(text="ignored")], model="m", parent_tool_use_id=None),
            _result(result="  recap text  "),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))
        assert claude_cli.run_sync("summarise", model="haiku", max_turns=1) == "recap text"

    def test_falls_back_to_assistant_text_when_no_result_field(self, monkeypatch):
        messages = [
            AssistantMessage(content=[TextBlock(text="ok")], model="m", parent_tool_use_id=None),
            _result(result=None),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))
        assert claude_cli.run_sync("x", model="haiku") == "ok"

    def test_raises_on_non_success(self, monkeypatch):
        monkeypatch.setattr(claude_cli, "query", _fake_query([_result(subtype="error_during_execution")]))
        with pytest.raises(RuntimeError, match="error_during_execution"):
            claude_cli.run_sync("x", model="haiku")


# ---------------------------------------------------------------------------
# Subscription-auth invariant: never set ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------


class TestSubscriptionAuthPreserved:
    def test_run_sync_does_not_set_anthropic_api_key(self, monkeypatch):
        # The SDK drives the `claude` CLI, inheriting subscription OAuth. Setting
        # ANTHROPIC_API_KEY would silently move billing to pay-as-you-go.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(claude_cli, "query", _fake_query([_result(result="ok")]))
        claude_cli.run_sync("x", model="haiku")
        assert "ANTHROPIC_API_KEY" not in os.environ

    def test_run_agent_does_not_set_anthropic_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(claude_cli, "query", _fake_query([_result()]))
        _run_agent("Begin.", model="sonnet")
        assert "ANTHROPIC_API_KEY" not in os.environ

    def test_build_options_smuggles_no_api_key_into_sdk_env(self):
        # Guard the other leak path: an api key injected via ClaudeAgentOptions.env.
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
        )
        env = opts.env or {}
        assert "ANTHROPIC_API_KEY" not in env


# ---------------------------------------------------------------------------
# Options building
# ---------------------------------------------------------------------------


class TestBuildOptions:
    def test_maps_core_fields(self):
        opts = claude_cli._build_options(
            model="haiku",
            system_prompt=None,
            permission_mode="acceptEdits",
            allowed_tools=None,
            mcp_config=None,
            max_turns=1,
            max_budget_usd=2.5,
            cwd=None,
        )
        assert opts.model == "haiku"
        assert opts.permission_mode == "acceptEdits"
        assert opts.max_turns == 1
        assert opts.max_budget_usd == 2.5

    def test_setting_sources_left_default(self):
        # None == load user+project+local (same as CLI). Guard against accidental
        # change to the default in _build_options.
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
        )
        assert opts.setting_sources is None

    def test_allowed_tools_string_split_to_list(self):
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools="Read,Write Edit",
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
        )
        assert opts.allowed_tools == ["Read", "Write", "Edit"]

    def test_tools_availability_restriction_passed_through(self):
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
            tools=["Read", "Write"],
        )
        assert opts.tools == ["Read", "Write"]

    def test_thinking_passed_through(self):
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
            thinking={"type": "disabled"},
        )
        assert opts.thinking == {"type": "disabled"}

    def test_effort_passed_through_when_set(self):
        opts = claude_cli._build_options(
            model="claude-sonnet-5",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
            effort="medium",
        )
        assert opts.effort == "medium"

    def test_effort_unset_by_default(self):
        # Omitting effort must NOT pin a value -- the SDK default applies. Haiku
        # used to 400 on effort (no longer on 0.2.110, see bin/sdk-canary), so it
        # stays absent unless a stage opts in.
        opts = claude_cli._build_options(
            model="claude-haiku-4-5",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
        )
        assert opts.effort is None


class TestPartialMessagesEnabled:
    def test_build_options_enables_partial_messages(self):
        # Partial StreamEvents densify the stream so the idle timer resets
        # mid-generation. The driver ignores their content, so callers never see
        # them -- only the idle timer benefits.
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
        )
        assert opts.include_partial_messages is True

    def test_build_options_passes_load_timeout(self):
        opts = claude_cli._build_options(
            model="sonnet",
            system_prompt=None,
            permission_mode=None,
            allowed_tools=None,
            mcp_config=None,
            max_turns=None,
            max_budget_usd=None,
            cwd=None,
            load_timeout_ms=5000,
        )
        assert opts.load_timeout_ms == 5000


# ---------------------------------------------------------------------------
# In-process hang detection: event-idle watchdog
# ---------------------------------------------------------------------------


def _stalling_query(closed_flag):
    """A query() stand-in whose async iterator never yields (hangs forever).

    Records aclose() so the test can prove the SDK generator is torn down on
    idle-timeout (no leaked subprocess).
    """

    async def _gen(*, prompt, options, transport=None):
        # Await an event that is never set -> __anext__ blocks indefinitely,
        # which is exactly what asyncio.wait_for must interrupt.
        never = asyncio.Event()
        try:
            await never.wait()
            yield  # pragma: no cover -- unreachable, makes this an async generator
        finally:
            closed_flag["closed"] = True

    return _gen


class TestIdleWatchdog:
    def test_idle_timeout_fires_when_stream_stalls(self, monkeypatch):
        closed = {"closed": False}
        monkeypatch.setattr(claude_cli, "query", _stalling_query(closed))

        with pytest.raises(RuntimeError, match="idle timeout"):
            _run_agent("Begin.", model="sonnet", idle_timeout=0.05)

        # The SDK generator was closed -> subprocess torn down, no leak.
        assert closed["closed"] is True

    def test_healthy_stream_completes_within_idle_timeout(self, monkeypatch):
        messages = [
            AssistantMessage(content=[TextBlock(text="hi")], model="m", parent_tool_use_id=None),
            _result(),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        res = _run_agent("Begin.", model="sonnet", idle_timeout=5.0)
        assert res.ok is True

    def test_idle_timer_resets_on_partial_stream_events(self, monkeypatch):
        # StreamEvents carry no result content, but each one must still RESET the
        # idle timer. We emit several partial events with a gap shorter than
        # idle_timeout, then a result. If they did not reset the timer this would
        # time out; it must complete cleanly.
        async def _gen(*, prompt, options, transport=None):
            for _ in range(5):
                await asyncio.sleep(0.02)
                yield StreamEvent(uuid="u", session_id="s", event={"type": "partial"})
            await asyncio.sleep(0.02)
            yield _result()

        monkeypatch.setattr(claude_cli, "query", _gen)

        # idle_timeout (0.1) > per-event gap (0.02) but < total stream time (0.12),
        # so this only passes if the timer resets on each filtered StreamEvent.
        res = _run_agent("Begin.", model="sonnet", idle_timeout=0.1)
        assert res.ok is True

    def test_run_sync_idle_timeout_also_fires(self, monkeypatch):
        # The text path shares the same watchdog.
        closed = {"closed": False}
        monkeypatch.setattr(claude_cli, "query", _stalling_query(closed))
        with pytest.raises(RuntimeError, match="idle timeout"):
            claude_cli.run_sync("x", model="haiku", idle_timeout=0.05)
        assert closed["closed"] is True
