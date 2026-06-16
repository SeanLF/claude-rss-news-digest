"""Tests for claude_cli.py SDK-message -> legacy-event adaptation.

These mock the Claude Agent SDK's query() so they never spend credit. They
assert that the dataclass messages the SDK yields are translated into the exact
dict shape claude.py consumes (event["type"], event["message"]["content"],
event.get("parent_tool_use_id"), event.get("total_cost_usd"), ...).
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import claude_cli
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Block / message adaptation (pure, no SDK invocation)
# ---------------------------------------------------------------------------


class TestBlockToDict:
    def test_text_block(self):
        assert claude_cli._block_to_dict(TextBlock(text="hi")) == {"type": "text", "text": "hi"}

    def test_tool_use_block_preserves_agent_fields(self):
        block = ToolUseBlock(id="tu_1", name="Agent", input={"description": "CLUSTER articles"})
        assert claude_cli._block_to_dict(block) == {
            "type": "tool_use",
            "id": "tu_1",
            "name": "Agent",
            "input": {"description": "CLUSTER articles"},
        }

    def test_tool_result_block(self):
        block = ToolResultBlock(tool_use_id="tu_1", content="done", is_error=False)
        out = claude_cli._block_to_dict(block)
        assert out["type"] == "tool_result"
        assert out["tool_use_id"] == "tu_1"
        assert out["content"] == "done"
        assert out["is_error"] is False


class TestMessageToEvent:
    def test_assistant_with_agent_tool_use(self):
        msg = AssistantMessage(
            content=[ToolUseBlock(id="tu_9", name="Agent", input={"description": "SELECT stories"})],
            model="claude-sonnet",
            parent_tool_use_id=None,
        )
        event = claude_cli._message_to_event(msg)
        assert event["type"] == "assistant"
        assert event["parent_tool_use_id"] is None
        block = event["message"]["content"][0]
        assert block["type"] == "tool_use"
        assert block["name"] == "Agent"
        assert block["id"] == "tu_9"
        assert block["input"]["description"] == "SELECT stories"

    def test_user_tool_result_at_root_has_no_parent(self):
        # Root-level tool_result (Agent returning to dispatcher): parent is None,
        # which is how claude.py distinguishes Agent completions.
        msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tu_9", content="ok", is_error=False)],
            parent_tool_use_id=None,
        )
        event = claude_cli._message_to_event(msg)
        assert event["type"] == "user"
        assert event.get("parent_tool_use_id") is None
        block = event["message"]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_9"

    def test_nested_user_tool_result_keeps_parent(self):
        msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="x", content="r", is_error=False)],
            parent_tool_use_id="tu_parent",
        )
        event = claude_cli._message_to_event(msg)
        assert event["parent_tool_use_id"] == "tu_parent"

    def test_result_success_carries_cost_and_duration(self):
        msg = ResultMessage(
            subtype="success",
            duration_ms=1234,
            duration_api_ms=1000,
            is_error=False,
            num_turns=3,
            session_id="s1",
            total_cost_usd=0.42,
        )
        event = claude_cli._message_to_event(msg)
        assert event["type"] == "result"
        assert event["subtype"] == "success"
        assert event["total_cost_usd"] == 0.42
        assert event["duration_ms"] == 1234

    def test_result_budget_cap_subtype(self):
        msg = ResultMessage(
            subtype="error_max_budget_usd",
            duration_ms=10,
            duration_api_ms=5,
            is_error=True,
            num_turns=1,
            session_id="s1",
        )
        event = claude_cli._message_to_event(msg)
        assert event["subtype"] == "error_max_budget_usd"

    def test_system_message_flattens_data(self):
        msg = SystemMessage(subtype="task_progress", data={"foo": "bar"})
        event = claude_cli._message_to_event(msg)
        assert event["type"] == "system"
        assert event["subtype"] == "task_progress"
        assert event["foo"] == "bar"


# ---------------------------------------------------------------------------
# Result-event validation helpers
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_success_passes(self):
        claude_cli._check_result([{"type": "result", "subtype": "success"}])

    def test_missing_result_raises(self):
        with pytest.raises(RuntimeError, match="no result event"):
            claude_cli._check_result([{"type": "assistant", "message": {"content": []}}])

    def test_budget_cap_raises_with_subtype(self):
        with pytest.raises(RuntimeError, match="error_max_budget_usd"):
            claude_cli._check_result([{"type": "result", "subtype": "error_max_budget_usd"}])


class TestResultText:
    def test_prefers_result_field(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ignored"}]}},
            {"type": "result", "subtype": "success", "result": "  final answer  "},
        ]
        assert claude_cli._result_text(events) == "final answer"

    def test_falls_back_to_assistant_text(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "result", "subtype": "success"},
        ]
        assert claude_cli._result_text(events) == "ok"


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


# ---------------------------------------------------------------------------
# End-to-end stream_sync with a mocked SDK query()
# ---------------------------------------------------------------------------


def _fake_query(messages):
    """Return an async-generator factory yielding the given SDK messages."""

    async def _gen(*, prompt, options, transport=None):
        for m in messages:
            yield m

    return _gen


class TestStreamSyncIntegration:
    def test_streams_dispatcher_events_in_legacy_shape(self, monkeypatch):
        messages = [
            AssistantMessage(
                content=[ToolUseBlock(id="tu_1", name="Agent", input={"description": "CLUSTER"})],
                model="claude-sonnet",
                parent_tool_use_id=None,
            ),
            UserMessage(
                content=[ToolResultBlock(tool_use_id="tu_1", content="clustered", is_error=False)],
                parent_tool_use_id=None,
            ),
            ResultMessage(
                subtype="success",
                duration_ms=2000,
                duration_api_ms=1500,
                is_error=False,
                num_turns=5,
                session_id="s1",
                total_cost_usd=1.23,
            ),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        events = list(claude_cli.stream_sync("Begin.", model="sonnet", permission_mode="acceptEdits"))

        assert [e["type"] for e in events] == ["assistant", "user", "result"]
        # Agent dispatch surfaces exactly as claude.py expects.
        assert events[0]["message"]["content"][0]["name"] == "Agent"
        assert events[0]["message"]["content"][0]["id"] == "tu_1"
        # Root tool_result has no parent -> claude.py marks the Agent complete.
        assert events[1].get("parent_tool_use_id") is None
        assert events[1]["message"]["content"][0]["tool_use_id"] == "tu_1"
        # Result carries cost + subtype for logging / success detection.
        assert events[2]["subtype"] == "success"
        assert events[2]["total_cost_usd"] == 1.23

    def test_run_sync_returns_result_text(self, monkeypatch):
        messages = [
            AssistantMessage(
                content=[TextBlock(text="ok")],
                model="claude-haiku",
                parent_tool_use_id=None,
            ),
            ResultMessage(
                subtype="success",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                total_cost_usd=0.0,
                result="recap text",
            ),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        out = claude_cli.run_sync("summarise", model="haiku", max_turns=1)
        assert out == "recap text"

    def test_run_sync_raises_on_non_success(self, monkeypatch):
        messages = [
            ResultMessage(
                subtype="error_during_execution",
                duration_ms=10,
                duration_api_ms=5,
                is_error=True,
                num_turns=1,
                session_id="s1",
            ),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        with pytest.raises(RuntimeError, match="error_during_execution"):
            claude_cli.run_sync("x", model="haiku")


# ---------------------------------------------------------------------------
# In-process hang detection: event-idle timeout
# ---------------------------------------------------------------------------


class TestPartialMessagesEnabled:
    def test_build_options_enables_partial_messages(self):
        # Partial StreamEvents densify the stream so the idle timer resets
        # mid-generation. _message_to_event filters them to None, so callers
        # never see them -- only the idle timer benefits.
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


class TestIdleTimeout:
    def test_idle_timeout_fires_when_stream_stalls(self, monkeypatch):
        closed = {"closed": False}
        monkeypatch.setattr(claude_cli, "query", _stalling_query(closed))

        with pytest.raises(RuntimeError, match="idle timeout"):
            list(claude_cli.stream_sync("/x", model="sonnet", idle_timeout=0.05))

        # The SDK generator was closed -> subprocess torn down, no leak.
        assert closed["closed"] is True

    def test_healthy_stream_completes_within_idle_timeout(self, monkeypatch):
        # Events arrive promptly (well under the idle window) -> normal completion.
        messages = [
            AssistantMessage(content=[TextBlock(text="hi")], model="m", parent_tool_use_id=None),
            ResultMessage(
                subtype="success",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                total_cost_usd=0.0,
            ),
        ]
        monkeypatch.setattr(claude_cli, "query", _fake_query(messages))

        events = list(claude_cli.stream_sync("/x", model="sonnet", idle_timeout=5.0))
        assert [e["type"] for e in events] == ["assistant", "result"]

    def test_idle_timer_resets_on_filtered_messages(self, monkeypatch):
        # StreamEvents are filtered to None by _message_to_event, but each one
        # must still RESET the idle timer. We emit several partial events with a
        # gap shorter than idle_timeout, then a result. If filtered messages did
        # not reset the timer this would time out; it must complete cleanly.
        async def _gen(*, prompt, options, transport=None):
            for _ in range(5):
                await asyncio.sleep(0.02)
                yield StreamEvent(uuid="u", session_id="s", event={"type": "partial"})
            await asyncio.sleep(0.02)
            yield ResultMessage(
                subtype="success",
                duration_ms=10,
                duration_api_ms=5,
                is_error=False,
                num_turns=1,
                session_id="s1",
                total_cost_usd=0.0,
            )

        monkeypatch.setattr(claude_cli, "query", _gen)

        # idle_timeout (0.1) > per-event gap (0.02) but < total stream time (0.12),
        # so this only passes if the timer resets on each filtered StreamEvent.
        events = list(claude_cli.stream_sync("/x", model="sonnet", idle_timeout=0.1))
        assert [e["type"] for e in events] == ["result"]
