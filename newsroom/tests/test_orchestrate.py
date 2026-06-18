"""Tests for orchestrate.py: deterministic Python orchestration of subagents.

The Claude Agent SDK cannot run inside this environment (CLAUDECODE=1 blocks
nested `claude -p`), so every test mocks ``claude_cli.run_agent`` and never makes
a real model call. The curation phase is async (one event loop, opened once by
claude.generate_selections); these sync test bodies drive the async functions via
asyncio.run, so no pytest-asyncio is needed. ``run_agent`` returns a
``claude_cli.StageResult``; orchestrate reads ``.ok`` / ``.usage`` /
``.total_cost_usd`` / ``.duration_ms`` off it. End-to-end validation happens later
in Docker.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
from claude_cli import StageResult

REPO_ROOT = Path(__file__).parent.parent.parent
CLUSTER_SPEC = REPO_ROOT / ".claude" / "agents" / "cluster.md"


def _stage_result(*, usage=None, cost=0.05, duration_ms=1000, subtype="success"):
    """Build a StageResult like ``claude_cli.run_agent`` resolves to."""
    return StageResult(
        subtype=subtype,
        text="",
        usage=usage
        or {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 8000,
        },
        total_cost_usd=cost,
        duration_ms=duration_ms,
    )


def _async_return(result):
    """An async ``run_agent`` stand-in that just resolves to ``result``."""

    async def _f(*_a, **_k):
        return result

    return _f


def _run_stage(*a, **k):
    """Drive the async ``run_stage`` from a sync test body."""
    return asyncio.run(orchestrate.run_stage(*a, **k))


def _orchestrate(*a, **k):
    """Drive the async ``orchestrate_selections`` from a sync test body."""
    return asyncio.run(orchestrate.orchestrate_selections(*a, **k))


# --------------------------------------------------------------------------- #
# parse_agent_spec
# --------------------------------------------------------------------------- #


class TestParseAgentSpec:
    def test_parses_real_cluster_spec(self):
        spec = orchestrate.parse_agent_spec(CLUSTER_SPEC)
        assert spec.name == "cluster"
        assert spec.model == "claude-sonnet-4-6"
        assert spec.tools_str == "Read, Write"
        # Body is the markdown system prompt, frontmatter stripped.
        assert spec.body.startswith("You are a news clustering agent")
        assert "name: cluster" not in spec.body
        assert "clusters.json" in spec.body

    def test_missing_frontmatter_raises(self, tmp_path):
        p = tmp_path / "bad.md"
        p.write_text("no frontmatter here")
        with pytest.raises(ValueError, match="missing frontmatter"):
            orchestrate.parse_agent_spec(p)

    def test_missing_name_raises(self, tmp_path):
        p = tmp_path / "bad.md"
        p.write_text("---\nmodel: claude-sonnet-4-6\n---\nbody")
        with pytest.raises(ValueError, match="missing 'name'"):
            orchestrate.parse_agent_spec(p)


# --------------------------------------------------------------------------- #
# run_stage
# --------------------------------------------------------------------------- #


def _spec():
    return orchestrate.AgentSpec(
        name="cluster",
        model="claude-sonnet-4-6",
        tools_str="Read, Write",
        body="system prompt",
    )


def _ok_validator(_dir):
    return None


def _fail_validator(_dir):
    raise ValueError("output never valid")


class TestRunStage:
    def test_builds_usage_row(self, tmp_path, monkeypatch):
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", _async_return(_stage_result(cost=0.4242)))

        row = _run_stage(
            _spec(),
            label="cluster",
            output_path=out,
            validate=_ok_validator,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )

        assert row["subagent"] == "cluster"
        assert row["model"] == "claude-sonnet-4-6"
        assert row["input_tokens"] == 1000
        assert row["output_tokens"] == 200
        assert row["cache_write_tokens"] == 500
        assert row["cache_read_tokens"] == 8000
        # Cost is the SDK's own total_cost_usd, not a hand-rolled token x rate sum.
        assert row["api_cost_usd"] == 0.4242

    def test_model_override_used(self, tmp_path, monkeypatch):
        out = tmp_path / "clusters.json"
        out.write_text("{}")
        seen = {}

        async def fake_run(*_a, **k):
            seen["model"] = k["model"]
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)

        row = _run_stage(
            _spec(),
            label="cluster",
            output_path=out,
            validate=_ok_validator,
            model_override="claude-haiku-4-5",
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert seen["model"] == "claude-haiku-4-5"
        assert row["model"] == "claude-haiku-4-5"

    def test_retries_once_then_raises_on_invalid_output(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)

        with pytest.raises(RuntimeError, match="failed after retry"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_fail_validator,  # always fails
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        assert calls["n"] == 2  # invoked exactly twice (1 + 1 retry)

    def test_retries_once_then_succeeds(self, tmp_path, monkeypatch):
        out = tmp_path / "clusters.json"
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                # First attempt: write nothing -> validator fails.
                return _stage_result()
            out.write_text(json.dumps({"clusters": [1]}))
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)

        def validate(_dir):
            if not out.exists():
                raise ValueError("missing")

        row = _run_stage(
            _spec(),
            label="cluster",
            output_path=out,
            validate=validate,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert calls["n"] == 2
        assert row["subagent"] == "cluster"

    def test_invocation_error_raises(self, tmp_path, monkeypatch):
        # run_agent raising (e.g. "no result event") propagates as a stage failure
        # after the once-retry.
        async def fake_run(*_a, **_k):
            raise RuntimeError("claude failed: no result event received")

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        with pytest.raises(RuntimeError, match="failed after retry"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )

    def test_non_success_subtype_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            orchestrate.claude_cli,
            "run_agent",
            _async_return(_stage_result(subtype="error_max_budget_usd")),
        )
        with pytest.raises(RuntimeError, match="failed after retry"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )

    def test_unexpected_exception_fails_loud_without_retry(self, tmp_path, monkeypatch):
        # A non-retryable, non-(RuntimeError|ValueError) error is a programming bug,
        # not a transient outage: it must propagate immediately and unwrapped -- NOT
        # be retried and NOT be folded into "failed after retry". Fail loud at the
        # boundary so the bug surfaces instead of masquerading as a flaky stage.
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            raise KeyError("unexpected bug")

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        with pytest.raises(KeyError, match="unexpected bug"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        assert calls["n"] == 1  # raised on first attempt, never retried


# --------------------------------------------------------------------------- #
# validators
# --------------------------------------------------------------------------- #


class TestValidators:
    def test_clusters_ok(self, tmp_path):
        (tmp_path / "clusters.json").write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        orchestrate.validate_clusters(tmp_path)  # no raise

    def test_clusters_empty_raises(self, tmp_path):
        (tmp_path / "clusters.json").write_text(json.dumps({"clusters": []}))
        with pytest.raises(ValueError, match="missing or empty"):
            orchestrate.validate_clusters(tmp_path)

    def test_recap_empty_raises(self, tmp_path):
        (tmp_path / "recap.txt").write_text("   \n")
        with pytest.raises(ValueError, match="empty"):
            orchestrate.validate_recap(tmp_path)

    def test_selected_ok(self, tmp_path):
        (tmp_path / "selected.json").write_text(json.dumps({"must_know": [], "should_know": []}))
        orchestrate.validate_selected(tmp_path)

    def test_draft_missing_preheader_raises(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(json.dumps({"must_know": [], "should_know": []}))
        with pytest.raises(ValueError, match="preheader"):
            orchestrate.validate_draft(tmp_path)

    def test_coherence_ok(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": []}))
        orchestrate.validate_coherence(tmp_path)


# --------------------------------------------------------------------------- #
# orchestrate_selections (happy path)
# --------------------------------------------------------------------------- #


class TestOrchestrateSelections:
    # Canned output per stage, keyed by a distinctive phrase from each agent's
    # body (its system prompt) so we write the correct file even though several
    # bodies mention multiple filenames. run_stage unlinks the stale file first,
    # so the agent must (re)write it each call. recap.txt is plain text.
    _STAGE_OUTPUTS = (
        ("news clustering agent", "clusters.json", {"clusters": [{"story": "x", "article_ids": ["A1"]}]}),
        ("recap summariser", "recap.txt", None),
        ("news editor", "selected.json", {"must_know": [], "should_know": []}),
        ("news writer", "draft_selections.json", {"must_know": [], "should_know": [], "preheader": "p"}),
        ("fact-checking editor", "coherence_report.json", {"results": []}),
    )

    def _fake_writer(self, claude_input_dir):
        """Return a run_agent stand-in that writes whichever output is due.

        Identifies the running agent by a unique phrase in its system prompt,
        writes that stage's output file, then resolves to a success StageResult.
        """

        async def fake_run(_prompt, *, system_prompt, **_k):
            for phrase, filename, payload in self._STAGE_OUTPUTS:
                if phrase in system_prompt:
                    path = claude_input_dir / filename
                    if payload is None:
                        path.write_text("A real weekly recap.")
                    else:
                        path.write_text(json.dumps(payload))
                    break
            return _stage_result()

        return fake_run

    def test_returns_five_rows_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", self._fake_writer(tmp_path))
        # Point the orchestrator's spec loader at the real agent files.
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        rows = _orchestrate(claude_input_dir=tmp_path)

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]
        assert all(r["api_cost_usd"] >= 0 for r in rows)

    def test_raises_if_a_stage_never_validates(self, tmp_path, monkeypatch):
        # cluster writes a valid file, but the recap agent writes nothing ->
        # recap fails after its retry and the run aborts.
        async def fake_run(_prompt, *, system_prompt, **_k):
            if "news clustering agent" in system_prompt:
                (tmp_path / "clusters.json").write_text(
                    json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]})
                )
            # recap agent intentionally writes nothing.
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        with pytest.raises(RuntimeError, match="recap stage failed"):
            _orchestrate(claude_input_dir=tmp_path)


# --------------------------------------------------------------------------- #
# Chaos / fault injection: prove the transient-overload reliability is real,
# not just asserted. We inject 529s into the agent invocation and verify the
# stage recovers (bounded backoff) or fails LOUD (-> run aborts -> alert fires).
# --------------------------------------------------------------------------- #


class TestChaosTransientOutage:
    def _spec(self):
        return orchestrate.parse_agent_spec(CLUSTER_SPEC)

    def _no_backoff_sleep(self, monkeypatch):
        # Skip the real async backoff sleeps so the test is instant, and drive a
        # fake monotonic clock so the wall-clock retry budget is reached
        # deterministically without waiting hours. The async path sleeps via
        # asyncio.sleep (not time.sleep), so that is what we stub.
        import retry

        async def _fake_sleep(_delay):
            return None

        monkeypatch.setattr(retry.asyncio, "sleep", _fake_sleep)
        clock = {"t": 0.0}

        def fake_monotonic():
            # Advance ~5min of "elapsed" per call so a 4h budget is exhausted
            # in a bounded number of attempts.
            clock["t"] += 300.0
            return clock["t"]

        monkeypatch.setattr(retry.time, "monotonic", fake_monotonic)
        return retry

    def test_recovers_after_transient_outage(self, tmp_path, monkeypatch):
        """A few 529s then success: the stage retries with backoff and recovers."""
        self._no_backoff_sleep(monkeypatch)
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            if calls["n"] <= 2:  # outage for the first two invocations
                raise RuntimeError("API Error: 529 overloaded_error")
            (tmp_path / "clusters.json").write_text(
                json.dumps({"clusters": [{"story": "S", "article_ids": ["A1"]}]}), encoding="utf-8"
            )
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        row = _run_stage(
            self._spec(),
            label="cluster",
            output_path=tmp_path / "clusters.json",
            validate=orchestrate.validate_clusters,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert row["subagent"] == "cluster"
        assert calls["n"] == 3  # two transient failures, recovered on the third

    def test_fails_loud_after_sustained_outage(self, tmp_path, monkeypatch):
        """A never-ending outage exhausts the backoff budget and raises (no silent pass)."""
        self._no_backoff_sleep(monkeypatch)
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("529 overloaded")

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        with pytest.raises(RuntimeError, match="failed after retry"):
            _run_stage(
                self._spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=orchestrate.validate_clusters,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        # Bounded + loud: with_retry_async rides the wall-clock budget (fake clock
        # advances ~5min/call) then gives up, and the outer loop retries once.
        # The budget is finite, so the invocation count is bounded -- no infinite
        # backoff storm and no silent pass.
        budget_attempts = orchestrate._STAGE_RETRY_BUDGET_S / 300.0
        assert 1 < calls["n"] <= (budget_attempts + 2) * 2

    def test_idle_timeout_hang_is_retryable_and_recovers(self, tmp_path, monkeypatch):
        """An SDK idle-timeout (hang) is treated as transient and the stage recovers."""
        self._no_backoff_sleep(monkeypatch)
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                # Mirror claude_cli's hang detector: a RuntimeError whose message
                # contains "idle timeout" (so retry.is_retryable matches).
                raise RuntimeError("SDK idle timeout: no event in 120.0s")
            (tmp_path / "clusters.json").write_text(
                json.dumps({"clusters": [{"story": "S", "article_ids": ["A1"]}]}), encoding="utf-8"
            )
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        row = _run_stage(
            self._spec(),
            label="cluster",
            output_path=tmp_path / "clusters.json",
            validate=orchestrate.validate_clusters,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert row["subagent"] == "cluster"
        assert calls["n"] == 2  # hung once, recovered on retry

    def test_non_transient_error_not_retried_to_budget(self, tmp_path, monkeypatch):
        """A non-retryable error must NOT burn the transient budget (no backoff storm)."""
        self._no_backoff_sleep(monkeypatch)
        calls = {"n": 0}

        async def fake_run(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("authentication failed")  # not in retryable patterns

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        with pytest.raises(RuntimeError, match="failed after retry"):
            _run_stage(
                self._spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=orchestrate.validate_clusters,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        # No transient backoff: just the outer once-retry -> exactly 2 invocations.
        assert calls["n"] == 2


# --- Resume checkpointing: skip a stage whose valid output already exists -----


def test_stage_output_is_valid_true_when_present_and_valid(tmp_path):
    (tmp_path / "clusters.json").write_text('{"clusters": [{"story": "A"}]}')
    assert orchestrate._stage_output_is_valid(tmp_path, "clusters.json", orchestrate.validate_clusters) is True


def test_stage_output_is_valid_false_when_missing(tmp_path):
    assert orchestrate._stage_output_is_valid(tmp_path, "clusters.json", orchestrate.validate_clusters) is False


def test_stage_output_is_valid_false_when_present_but_invalid(tmp_path):
    # Present but empty -> validator raises -> stage must re-run, not be skipped.
    (tmp_path / "clusters.json").write_text('{"clusters": []}')
    assert orchestrate._stage_output_is_valid(tmp_path, "clusters.json", orchestrate.validate_clusters) is False
