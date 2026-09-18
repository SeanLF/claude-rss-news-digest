"""The retry layer's invariants, which had no test file.

Three, in rising order of how quietly they fail:

1. One budget is shared across a stage's attempts and across the run's stages. Per-stage
   budgets once summed to 28h under a 5h systemd ceiling, and systemd's kill has no handler,
   so the run row stays 'running' forever.
2. A caller-supplied deadline beats the default elapsed budget, which is what makes (1) work.
3. A stage whose attempt exceeds its timeout is retried. This was NOT true when this file was
   written: asyncio.wait_for raises a bare TimeoutError, which is in neither
   with_retry_async's `except (RuntimeError, ClaudeSDKError)` nor run_stage's
   `except (RuntimeError, ValueError)`, so the documented "retried ONCE from a clean slate"
   contract did not cover the one failure the attempt timeout exists to catch.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
import retry

SYSTEMD_TIMEOUT_START_SEC = 5 * 3600


_REAL_SLEEP = asyncio.sleep


def _no_sleep(monkeypatch):
    # retry.asyncio IS the asyncio module, so this patches it globally -- capture the real
    # sleep first or the replacement calls itself.
    monkeypatch.setattr(retry.asyncio, "sleep", lambda _d: _REAL_SLEEP(0))


class TestTheRunBudgetFitsUnderSystemd:
    def test_run_budget_is_under_the_systemd_start_timeout(self):
        # The real ceiling is terraform's TimeoutStartSec, outside this repo; this pins the
        # half that lives here. If the unit's value changes, this is what to update.
        assert orchestrate._RUN_RETRY_BUDGET_S < SYSTEMD_TIMEOUT_START_SEC

    def test_a_single_stage_cannot_outlast_the_run(self):
        assert orchestrate._STAGE_RETRY_BUDGET_S <= orchestrate._RUN_RETRY_BUDGET_S

    def test_one_attempt_is_bounded_well_inside_the_stage_budget(self):
        assert orchestrate._STAGE_ATTEMPT_TIMEOUT_S < orchestrate._STAGE_RETRY_BUDGET_S


class TestASharedDeadlineBeatsAFreshBudget:
    def test_a_caller_deadline_overrides_max_elapsed(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = {"n": 0}

        async def always_overloaded():
            calls["n"] += 1
            raise RuntimeError("529 overloaded")

        with pytest.raises(RuntimeError, match="overloaded"):
            asyncio.run(
                retry.with_retry_async(
                    always_overloaded,
                    label="t",
                    max_elapsed=14400.0,
                    deadline=retry.time.monotonic() - 1,
                )
            )
        assert calls["n"] == 1

    def test_without_a_deadline_it_retries(self, monkeypatch):
        # Negative control: the single attempt above is the deadline's doing, not a retry
        # loop that never runs.
        _no_sleep(monkeypatch)
        calls = {"n": 0}

        async def overloaded_then_ok():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("529 overloaded")
            return "ok"

        assert asyncio.run(retry.with_retry_async(overloaded_then_ok, label="t")) == "ok"
        assert calls["n"] == 3

    def test_a_non_retryable_error_is_not_retried(self):
        calls = {"n": 0}

        async def bad_shape():
            calls["n"] += 1
            raise RuntimeError("selected.json is not valid JSON")

        with pytest.raises(RuntimeError, match="not valid JSON"):
            asyncio.run(retry.with_retry_async(bad_shape, label="t"))
        assert calls["n"] == 1


class TestAnAttemptTimeoutIsRetried:
    """One clean retry -- the contract run_stage documents for every other failure."""

    @staticmethod
    def _spec():
        return orchestrate.AgentSpec(name="select", model="claude-sonnet-4-6", tools_str="Read, Write", body="b")

    def _run(self, tmp_path, monkeypatch, invocations, *, succeed_on=None):
        out = tmp_path / "selected.json"

        async def fake_invoke(*_a, **_k):
            invocations.append(1)
            if succeed_on is not None and len(invocations) >= succeed_on:
                out.write_text('{"must_know": [], "should_know": []}')
                return orchestrate.claude_cli.StageResult(
                    subtype="success", text="", usage={}, total_cost_usd=0.0, duration_ms=1
                )
            # Never completes. A plain asyncio.sleep would be neutered by _no_sleep, which
            # patches the module globally -- the fake would return instantly and nothing
            # would time out.
            await asyncio.Event().wait()

        monkeypatch.setattr(orchestrate, "_invoke_agent", fake_invoke)
        monkeypatch.setattr(orchestrate, "_STAGE_ATTEMPT_TIMEOUT_S", 0.01)
        _no_sleep(monkeypatch)
        return asyncio.run(
            orchestrate.run_stage(
                self._spec(),
                label="select",
                output_path=out,
                validate=lambda _d: None,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        )

    def test_a_timed_out_attempt_is_retried(self, tmp_path, monkeypatch):
        invocations: list[int] = []
        with pytest.raises(RuntimeError):
            self._run(tmp_path, monkeypatch, invocations)
        assert len(invocations) > 1, "a timed-out attempt was not retried"

    def test_the_failure_names_the_stage(self, tmp_path, monkeypatch):
        # A bare TimeoutError tells an operator nothing about WHICH stage hung.
        with pytest.raises(RuntimeError, match="select"):
            self._run(tmp_path, monkeypatch, [])

    def test_a_stage_that_recovers_on_the_retry_succeeds(self, tmp_path, monkeypatch):
        invocations: list[int] = []
        row = self._run(tmp_path, monkeypatch, invocations, succeed_on=2)
        assert row["subagent"] == "select"
