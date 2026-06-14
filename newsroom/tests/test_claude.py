"""Tests for claude.py retry behaviour on transient API failures."""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeSDKError, ProcessError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import claude


class TestIsRetryable:
    def test_matches_overloaded_error_name(self):
        assert claude._is_retryable(RuntimeError("529 overloaded_error"))

    def test_matches_overloaded_case_insensitive(self):
        assert claude._is_retryable(RuntimeError("Overloaded"))

    def test_matches_rate_limit(self):
        assert claude._is_retryable(RuntimeError("rate_limit exceeded"))

    def test_matches_529_status_in_stderr(self):
        assert claude._is_retryable(RuntimeError("claude failed (exit 1): API Error: 529"))

    def test_matches_503_bad_gateway(self):
        assert claude._is_retryable(RuntimeError("Bad Gateway (503)"))

    def test_matches_502(self):
        assert claude._is_retryable(RuntimeError("HTTP 502 from upstream"))

    def test_matches_timeout(self):
        assert claude._is_retryable(RuntimeError("read timeout after 60s"))

    def test_rejects_auth_error(self):
        assert not claude._is_retryable(RuntimeError("authentication failed"))

    def test_rejects_generic_failure(self):
        assert not claude._is_retryable(RuntimeError("invalid JSON output"))


class TestWithRetry:
    def test_returns_first_try_when_successful(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert claude._with_retry(fn, label="t") == "ok"
        assert calls == [1]

    def test_raises_immediately_on_non_retryable(self):
        def fn():
            raise RuntimeError("authentication failed")

        with pytest.raises(RuntimeError, match="authentication failed"):
            claude._with_retry(fn, label="t")

    @patch("claude.time.sleep")
    def test_retries_on_overloaded_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("claude failed (exit 1): 529 overloaded_error")
            return "ok"

        assert claude._with_retry(fn, label="t") == "ok"
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("claude.time.sleep")
    def test_gives_up_after_max_attempts(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("overloaded")

        with pytest.raises(RuntimeError, match="overloaded"):
            claude._with_retry(fn, label="t", max_attempts=3)
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("claude.random.uniform", return_value=0.0)
    @patch("claude.time.sleep")
    def test_backoff_doubles_each_attempt(self, mock_sleep, _mock_uniform):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 4:
                raise RuntimeError("rate_limit")
            return "ok"

        claude._with_retry(fn, label="t")
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [30.0, 60.0, 120.0]

    @patch("claude.time.sleep")
    def test_caps_at_max_delay(self, mock_sleep):
        def fn():
            raise RuntimeError("overloaded")

        with pytest.raises(RuntimeError):
            claude._with_retry(fn, label="t", max_attempts=10)
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        # All delays must respect the cap (allowing for jitter ceiling).
        assert all(d <= claude._MAX_DELAY * (1 + claude._JITTER) + 0.01 for d in delays)

    @patch("claude.time.sleep")
    def test_on_retry_fires_between_attempts(self, _mock_sleep):
        calls = []
        on_retry_log = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("overloaded")
            return "ok"

        claude._with_retry(fn, label="t", on_retry=lambda: on_retry_log.append(len(calls)))
        assert on_retry_log == [1, 2]  # fires after attempts 1 and 2, before 2 and 3

    @patch("claude.time.sleep")
    def test_logs_recovery_after_retry(self, _mock_sleep, caplog):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("overloaded")
            return "ok"

        with caplog.at_level(logging.INFO, logger="claude"):
            claude._with_retry(fn, label="dispatcher")
        assert "recovered after 2 attempts" in caplog.text

    @patch("claude.time.sleep")
    def test_no_recovery_log_on_first_try_success(self, _mock_sleep, caplog):
        with caplog.at_level(logging.INFO, logger="claude"):
            claude._with_retry(lambda: "ok", label="dispatcher")
        assert "recovered" not in caplog.text


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
        assert claude._is_retryable(err)

    def test_is_retryable_matches_base_sdk_error(self):
        assert claude._is_retryable(ClaudeSDKError("upstream returned 529 overloaded"))

    @patch("claude.time.sleep")
    def test_retries_on_sdk_process_error_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ProcessError("claude failed", exit_code=1, stderr="529 overloaded_error")
            return "ok"

        assert claude._with_retry(fn, label="t") == "ok"
        assert len(calls) == 3
        assert mock_sleep.call_count == 2

    @patch("claude.time.sleep")
    def test_retries_on_base_sdk_error_then_succeeds(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ClaudeSDKError("API Error: 529 overloaded")
            return "ok"

        assert claude._with_retry(fn, label="t") == "ok"
        assert len(calls) == 2
        assert mock_sleep.call_count == 1

    @patch("claude.time.sleep")
    def test_does_not_retry_non_retryable_sdk_error(self, mock_sleep):
        calls = []

        def fn():
            calls.append(1)
            raise ClaudeSDKError("authentication failed: invalid credentials")

        with pytest.raises(ClaudeSDKError, match="authentication failed"):
            claude._with_retry(fn, label="t")
        assert len(calls) == 1  # raised immediately, no retry
        assert mock_sleep.call_count == 0


class TestGenerateSelections:
    @patch("claude._cleanup_dispatcher_intermediates")
    @patch("claude.stream_sync")
    @patch("claude.time.sleep")
    def test_retries_dispatcher_then_succeeds(self, _mock_sleep, mock_stream, mock_cleanup):
        call_count = {"n": 0}

        def side_effect(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("claude failed (exit 1): 529 overloaded_error")
            return iter(
                [
                    {"type": "result", "subtype": "success", "total_cost_usd": 0.1, "duration_ms": 1000},
                ]
            )

        mock_stream.side_effect = side_effect

        claude.generate_selections()

        assert call_count["n"] == 2
        mock_cleanup.assert_called_once()  # cleanup between attempts

    @patch("claude.stream_sync")
    def test_non_retryable_dispatcher_error_propagates(self, mock_stream):
        mock_stream.side_effect = RuntimeError("invalid configuration")

        with pytest.raises(RuntimeError, match="invalid configuration"):
            claude.generate_selections()
