"""Tests for claude.py retry behaviour on transient API failures."""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from claude_agent_sdk import ClaudeSDKError, ProcessError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import claude
import retry


class TestIsRetryable:
    def test_matches_overloaded_error_name(self):
        assert retry.is_retryable(RuntimeError("529 overloaded_error"))

    def test_matches_overloaded_case_insensitive(self):
        assert retry.is_retryable(RuntimeError("Overloaded"))

    def test_matches_rate_limit(self):
        assert retry.is_retryable(RuntimeError("rate_limit exceeded"))

    def test_matches_529_status_in_stderr(self):
        assert retry.is_retryable(RuntimeError("claude failed (exit 1): API Error: 529"))

    def test_matches_503_bad_gateway(self):
        assert retry.is_retryable(RuntimeError("Bad Gateway (503)"))

    def test_matches_502(self):
        assert retry.is_retryable(RuntimeError("HTTP 502 from upstream"))

    def test_matches_timeout(self):
        assert retry.is_retryable(RuntimeError("read timeout after 60s"))

    def test_matches_api_error_status_429(self):
        # api_error_status from a "success"-subtype-but-errored ResultMessage:
        # 429 (rate limit) must be retryable even though the word isn't present.
        assert retry.is_retryable(RuntimeError("CLUSTER: subtype='success' api_error_status=429"))

    def test_matches_api_error_status_500(self):
        assert retry.is_retryable(RuntimeError("WRITE: subtype='success' api_error_status=500"))

    def test_rejects_non_transient_api_error_status(self):
        # 400/401/403 are client errors, not transient: they must fail fast, not retry.
        assert not retry.is_retryable(RuntimeError("WRITE: subtype='success' api_error_status=403"))
        assert not retry.is_retryable(RuntimeError("WRITE: subtype='success' api_error_status=400"))

    def test_bare_500_not_from_status_does_not_retry(self):
        # The 500/504/429 patterns are anchored to "api_error_status=" so an
        # incidental number (a token count, a cost) cannot trigger a spurious retry.
        assert not retry.is_retryable(RuntimeError("wrote 500 articles, cost $0.0429"))

    def test_rejects_auth_error(self):
        assert not retry.is_retryable(RuntimeError("authentication failed"))

    def test_rejects_generic_failure(self):
        assert not retry.is_retryable(RuntimeError("invalid JSON output"))


class TestWithRetry:
    def test_returns_first_try_when_successful(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert retry.with_retry(fn, label="t") == "ok"
        assert calls == [1]

    def test_raises_immediately_on_non_retryable(self):
        def fn():
            raise RuntimeError("authentication failed")

        with pytest.raises(RuntimeError, match="authentication failed"):
            retry.with_retry(fn, label="t")

    @patch("retry.time.sleep")
    def test_retries_on_overloaded_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("claude failed (exit 1): 529 overloaded_error")
            return "ok"

        assert retry.with_retry(fn, label="t") == "ok"
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("retry.time.sleep")
    def test_gives_up_after_max_attempts(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("overloaded")

        with pytest.raises(RuntimeError, match="overloaded"):
            retry.with_retry(fn, label="t", max_attempts=3)
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("retry.random.uniform", return_value=0.0)
    @patch("retry.time.sleep")
    def test_backoff_doubles_each_attempt(self, mock_sleep, _mock_uniform):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 4:
                raise RuntimeError("rate_limit")
            return "ok"

        retry.with_retry(fn, label="t")
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [30.0, 60.0, 120.0]

    @patch("retry.time.sleep")
    def test_caps_at_max_delay(self, mock_sleep):
        def fn():
            raise RuntimeError("overloaded")

        with pytest.raises(RuntimeError):
            retry.with_retry(fn, label="t", max_attempts=10)
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        # All delays must respect the cap (allowing for jitter ceiling).
        assert all(d <= retry._MAX_DELAY * (1 + retry._JITTER) + 0.01 for d in delays)

    @patch("retry.time.sleep")
    def test_on_retry_fires_between_attempts(self, _mock_sleep):
        calls = []
        on_retry_log = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("overloaded")
            return "ok"

        retry.with_retry(fn, label="t", on_retry=lambda: on_retry_log.append(len(calls)))
        assert on_retry_log == [1, 2]  # fires after attempts 1 and 2, before 2 and 3

    @patch("retry.time.sleep")
    def test_logs_recovery_after_retry(self, _mock_sleep, caplog):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("overloaded")
            return "ok"

        with caplog.at_level(logging.INFO, logger="retry"):
            retry.with_retry(fn, label="dispatcher")
        assert "recovered after 2 attempts" in caplog.text

    @patch("retry.time.sleep")
    def test_no_recovery_log_on_first_try_success(self, _mock_sleep, caplog):
        with caplog.at_level(logging.INFO, logger="retry"):
            retry.with_retry(lambda: "ok", label="dispatcher")
        assert "recovered" not in caplog.text


class TestWithRetryWallClockBudget:
    """The default termination is a wall-clock budget (max_elapsed), not a fixed
    attempt count. These drive a fake monotonic clock so a multi-hour budget is
    exercised deterministically without real waits.
    """

    def _fake_clock(self, monkeypatch, step=300.0):
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += step
            return clock["t"]

        monkeypatch.setattr(retry.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(retry.time, "sleep", lambda *_a, **_k: None)
        return clock

    def test_retries_transient_until_success(self, monkeypatch):
        self._fake_clock(monkeypatch)
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 4:
                raise RuntimeError("529 overloaded")
            return "ok"

        assert retry.with_retry(fn, label="t") == "ok"
        assert len(calls) == 4

    def test_gives_up_after_max_elapsed_and_raises_last_error(self, monkeypatch):
        # Clock advances 5min/call; a tiny budget is exhausted almost immediately.
        self._fake_clock(monkeypatch, step=300.0)

        def fn():
            raise RuntimeError("529 overloaded budget-test")

        with pytest.raises(RuntimeError, match="budget-test"):
            retry.with_retry(fn, label="t", max_elapsed=600.0)

    def test_explicit_deadline_overrides_max_elapsed(self, monkeypatch):
        # A shared absolute deadline (as run_stage passes across its two attempts)
        # governs give-up even with a huge max_elapsed -- so the budget is not doubled.
        self._fake_clock(monkeypatch, step=300.0)
        monkeypatch.setattr(retry.random, "uniform", lambda *_a, **_k: 0.0)

        def fn():
            raise RuntimeError("529 overloaded deadline-test")

        with pytest.raises(RuntimeError, match="deadline-test"):
            retry.with_retry(fn, label="t", max_elapsed=10**9, deadline=601.0)

    def test_non_retryable_not_retried(self, monkeypatch):
        self._fake_clock(monkeypatch)
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("authentication failed")

        with pytest.raises(RuntimeError, match="authentication failed"):
            retry.with_retry(fn, label="t")
        assert len(calls) == 1

    def test_idle_timeout_is_retryable(self):
        # The SDK event-idle hang detector raises "...idle timeout...": must retry.
        assert retry.is_retryable(RuntimeError("SDK idle timeout: no event in 120.0s"))


class TestWithRetryAsync:
    """``with_retry_async`` is the async sibling of ``with_retry`` -- same backoff
    decision logic, but it ``await``s an async fn and ``await asyncio.sleep``s.
    The async orchestration path (orchestrate.run_stage) uses it so the whole
    curation phase runs under one event loop. We drive it with asyncio.run and
    stub the async sleep so there are no real waits.
    """

    def _no_sleep(self, monkeypatch):
        async def _fake_sleep(_delay):
            return None

        monkeypatch.setattr(retry.asyncio, "sleep", _fake_sleep)

    def _fake_clock(self, monkeypatch, step=300.0):
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += step
            return clock["t"]

        monkeypatch.setattr(retry.time, "monotonic", fake_monotonic)
        return clock

    def test_returns_first_try_when_successful(self, monkeypatch):
        self._no_sleep(monkeypatch)
        calls = []

        async def fn():
            calls.append(1)
            return "ok"

        assert asyncio.run(retry.with_retry_async(fn, label="t")) == "ok"
        assert calls == [1]

    def test_raises_immediately_on_non_retryable(self, monkeypatch):
        self._no_sleep(monkeypatch)

        async def fn():
            raise RuntimeError("authentication failed")

        with pytest.raises(RuntimeError, match="authentication failed"):
            asyncio.run(retry.with_retry_async(fn, label="t"))

    def test_retries_on_overloaded_then_succeeds(self, monkeypatch):
        self._no_sleep(monkeypatch)
        slept = []

        async def _count_sleep(delay):
            slept.append(delay)

        monkeypatch.setattr(retry.asyncio, "sleep", _count_sleep)
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("529 overloaded_error")
            return "ok"

        assert asyncio.run(retry.with_retry_async(fn, label="t")) == "ok"
        assert len(calls) == 3
        assert len(slept) == 2

    def test_gives_up_after_max_attempts(self, monkeypatch):
        self._no_sleep(monkeypatch)
        calls = []

        async def fn():
            calls.append(1)
            raise RuntimeError("overloaded")

        with pytest.raises(RuntimeError, match="overloaded"):
            asyncio.run(retry.with_retry_async(fn, label="t", max_attempts=3))
        assert len(calls) == 3

    def test_gives_up_after_max_elapsed_and_raises_last_error(self, monkeypatch):
        self._no_sleep(monkeypatch)
        self._fake_clock(monkeypatch, step=300.0)

        async def fn():
            raise RuntimeError("529 overloaded budget-test")

        with pytest.raises(RuntimeError, match="budget-test"):
            asyncio.run(retry.with_retry_async(fn, label="t", max_elapsed=600.0))

    def test_explicit_deadline_overrides_max_elapsed(self, monkeypatch):
        self._no_sleep(monkeypatch)
        self._fake_clock(monkeypatch, step=300.0)
        monkeypatch.setattr(retry.random, "uniform", lambda *_a, **_k: 0.0)

        async def fn():
            raise RuntimeError("529 overloaded deadline-test")

        with pytest.raises(RuntimeError, match="deadline-test"):
            asyncio.run(retry.with_retry_async(fn, label="t", max_elapsed=10**9, deadline=601.0))

    def test_on_retry_fires_between_attempts(self, monkeypatch):
        self._no_sleep(monkeypatch)
        calls = []
        fired = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("overloaded")
            return "ok"

        asyncio.run(retry.with_retry_async(fn, label="t", on_retry=lambda: fired.append(len(calls))))
        assert fired == [1, 2]


class TestRetryOnRealSdkErrors:
    """Regression: the Agent SDK raises ClaudeSDKError (NOT RuntimeError) on
    529/overload. _with_retry's except clause must catch the SDK type, or the
    retry guardrail is dead. These tests lock that contract using the real
    claude_agent_sdk exception classes, not RuntimeError stand-ins.
    """

    def test_is_retryable_matches_real_sdk_overload_error(self):
        # ProcessError folds stderr into its message; a 529/overload stderr
        # must be recognised as retryable.
        err = ProcessError("claude failed", exit_code=1, stderr="API Error: 529 overloaded_error")
        assert retry.is_retryable(err)

    def test_is_retryable_matches_base_sdk_error(self):
        assert retry.is_retryable(ClaudeSDKError("upstream returned 529 overloaded"))

    @patch("retry.time.sleep")
    def test_retries_on_sdk_process_error_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ProcessError("claude failed", exit_code=1, stderr="529 overloaded_error")
            return "ok"

        assert retry.with_retry(fn, label="t") == "ok"
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("retry.time.sleep")
    def test_retries_on_base_sdk_error_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ClaudeSDKError("API Error: 529 overloaded")
            return "ok"

        assert retry.with_retry(fn, label="t") == "ok"
        assert len(calls) == 2
        assert mock_sleep.call_count == 1

    @patch("retry.time.sleep")
    def test_does_not_retry_non_retryable_sdk_error(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            raise ClaudeSDKError("authentication failed: invalid credentials")

        with pytest.raises(ClaudeSDKError, match="authentication failed"):
            retry.with_retry(fn, label="t")
        assert len(calls) == 1  # raised immediately, no retry
        assert mock_sleep.call_count == 0


class TestModelConfig:
    """Model selection is centralized in config.py (env-overridable), not hardcoded
    at the call sites. Per-stage curation models still live in agent frontmatter;
    these cover the two standalone calls (weekly recap, health check) and the
    config defaults.
    """

    def test_config_exposes_model_defaults(self):
        import config

        assert config.DEFAULT_MODEL == "claude-sonnet-4-6"
        assert config.RECAP_MODEL == "claude-haiku-4-5"

    @patch("claude.run_sync")
    def test_weekly_recap_uses_configured_recap_model(self, mock_run_sync, monkeypatch):
        monkeypatch.setattr(claude.config, "RECAP_MODEL", "sentinel-recap-model")
        mock_run_sync.return_value = "a recap"

        claude.generate_weekly_recap("some titles")

        _, kwargs = mock_run_sync.call_args
        assert kwargs["model"] == "sentinel-recap-model"

    @patch("claude.run_sync")
    def test_health_check_uses_configured_default_model(self, mock_run_sync, monkeypatch):
        monkeypatch.setattr(claude.config, "DEFAULT_MODEL", "sentinel-default-model")
        mock_run_sync.return_value = "ok"

        assert claude.health_check() == 0

        _, kwargs = mock_run_sync.call_args
        assert kwargs["model"] == "sentinel-default-model"


class TestGenerateSelections:
    """generate_selections delegates to the async orchestrate.orchestrate_selections.

    It is the single sync/async boundary: a lone ``asyncio.run`` opening one event
    loop for the whole curation phase. orchestrate_selections is a coroutine, so
    these tests patch it with an AsyncMock. Per-stage retry/validation lives in
    orchestrate (see test_orchestrate.py); here we only assert the delegation
    contract: rows are returned, the model override + CLAUDE_INPUT_DIR are passed
    through, and errors propagate across the boundary.
    """

    @patch("claude.orchestrate_selections", new_callable=AsyncMock)
    def test_returns_usage_rows_and_passes_model(self, mock_orchestrate):
        rows = [{"subagent": "cluster", "api_cost_usd": 0.1}]
        mock_orchestrate.return_value = rows

        result = claude.generate_selections(model="claude-sonnet-4-6")

        assert result is rows
        _, kwargs = mock_orchestrate.call_args
        assert kwargs["model_override"] == "claude-sonnet-4-6"
        assert kwargs["claude_input_dir"] == claude.CLAUDE_INPUT_DIR

    @patch("claude.orchestrate_selections", new_callable=AsyncMock)
    def test_default_model_is_none(self, mock_orchestrate):
        mock_orchestrate.return_value = []

        claude.generate_selections()

        _, kwargs = mock_orchestrate.call_args
        assert kwargs["model_override"] is None

    @patch("claude.orchestrate_selections", new_callable=AsyncMock)
    def test_stage_failure_propagates(self, mock_orchestrate):
        mock_orchestrate.side_effect = RuntimeError("cluster stage failed after retry")

        with pytest.raises(RuntimeError, match="cluster stage failed"):
            claude.generate_selections()
