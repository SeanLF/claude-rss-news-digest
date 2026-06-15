"""Tests for orchestrate.py: deterministic Python orchestration of subagents.

The Claude Agent SDK cannot run inside this environment (CLAUDECODE=1 blocks
nested `claude -p`), so every test mocks ``claude_cli.stream_sync`` and never
makes a real model call. End-to-end validation happens later in Docker.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate

REPO_ROOT = Path(__file__).parent.parent.parent
CLUSTER_SPEC = REPO_ROOT / ".claude" / "agents" / "cluster.md"


def _result_event(*, usage=None, cost=0.05, duration_ms=1000, subtype="success"):
    """Build a terminal `result` event like the SDK wrapper yields."""
    return {
        "type": "result",
        "subtype": subtype,
        "total_cost_usd": cost,
        "duration_ms": duration_ms,
        "usage": usage
        or {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 8000,
        },
    }


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

        monkeypatch.setattr(
            orchestrate.claude_cli,
            "stream_sync",
            lambda *a, **k: iter([_result_event()]),
        )

        row = orchestrate.run_stage(
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
        # Sonnet cache_read pricing 0.30/M -> 8000 * 0.30 / 1e6 dominates the row.
        assert row["api_cost_usd"] > 0

    def test_model_override_used(self, tmp_path, monkeypatch):
        out = tmp_path / "clusters.json"
        out.write_text("{}")
        seen = {}

        def fake_stream(*_a, **k):
            seen["model"] = k["model"]
            return iter([_result_event()])

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)

        row = orchestrate.run_stage(
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

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            return iter([_result_event()])

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)

        with pytest.raises(RuntimeError, match="failed after retry"):
            orchestrate.run_stage(
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

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                # First attempt: write nothing -> validator fails.
                return iter([_result_event()])
            out.write_text(json.dumps({"clusters": [1]}))
            return iter([_result_event()])

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)

        def validate(_dir):
            if not out.exists():
                raise ValueError("missing")

        row = orchestrate.run_stage(
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

    def test_no_result_event_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            orchestrate.claude_cli,
            "stream_sync",
            lambda *a, **k: iter([{"type": "assistant", "message": {"content": []}}]),
        )
        with pytest.raises(RuntimeError, match="failed after retry"):
            orchestrate.run_stage(
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
            "stream_sync",
            lambda *a, **k: iter([_result_event(subtype="error_max_budget_usd")]),
        )
        with pytest.raises(RuntimeError, match="failed after retry"):
            orchestrate.run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )


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
        """Return a stream_sync stand-in that writes whichever output is due.

        Identifies the running agent by a unique phrase in its system prompt,
        writes that stage's output file, then yields a success result.
        """

        def fake_stream(_prompt, *, system_prompt, **_k):
            for phrase, filename, payload in self._STAGE_OUTPUTS:
                if phrase in system_prompt:
                    path = claude_input_dir / filename
                    if payload is None:
                        path.write_text("A real weekly recap.")
                    else:
                        path.write_text(json.dumps(payload))
                    break
            return iter([_result_event()])

        return fake_stream

    def test_returns_five_rows_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", self._fake_writer(tmp_path))
        # Point the orchestrator's spec loader at the real agent files.
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        rows = orchestrate.orchestrate_selections(claude_input_dir=tmp_path)

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]
        assert all(r["api_cost_usd"] >= 0 for r in rows)

    def test_raises_if_a_stage_never_validates(self, tmp_path, monkeypatch):
        # cluster writes a valid file, but the recap agent writes nothing ->
        # recap fails after its retry and the run aborts.
        def fake_stream(_prompt, *, system_prompt, **_k):
            if "news clustering agent" in system_prompt:
                (tmp_path / "clusters.json").write_text(
                    json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]})
                )
            # recap agent intentionally writes nothing.
            return iter([_result_event()])

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        with pytest.raises(RuntimeError, match="recap stage failed"):
            orchestrate.orchestrate_selections(claude_input_dir=tmp_path)


# --------------------------------------------------------------------------- #
# Chaos / fault injection: prove the transient-overload reliability is real,
# not just asserted. We inject 529s into the agent invocation and verify the
# stage recovers (bounded backoff) or fails LOUD (-> run aborts -> alert fires).
# --------------------------------------------------------------------------- #


class TestChaosTransientOutage:
    def _spec(self):
        return orchestrate.parse_agent_spec(CLUSTER_SPEC)

    def _no_backoff_sleep(self, monkeypatch):
        # Skip the real exponential-backoff sleeps so the test is instant, and
        # drive a fake monotonic clock so the wall-clock retry budget is reached
        # deterministically without waiting hours.
        import retry

        monkeypatch.setattr(retry.time, "sleep", lambda *_a, **_k: None)
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

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            if calls["n"] <= 2:  # outage for the first two invocations
                raise RuntimeError("API Error: 529 overloaded_error")
            (tmp_path / "clusters.json").write_text(
                json.dumps({"clusters": [{"story": "S", "article_ids": ["A1"]}]}), encoding="utf-8"
            )
            yield _result_event()

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)
        row = orchestrate.run_stage(
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

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("529 overloaded")
            yield  # pragma: no cover -- makes this a generator

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)
        with pytest.raises(RuntimeError, match="failed after retry"):
            orchestrate.run_stage(
                self._spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=orchestrate.validate_clusters,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        # Bounded + loud: with_retry rides the wall-clock budget (fake clock
        # advances ~5min/call) then gives up, and the outer loop retries once.
        # The budget is finite, so the invocation count is bounded -- no infinite
        # backoff storm and no silent pass.
        budget_attempts = orchestrate._STAGE_RETRY_BUDGET_S / 300.0
        assert 1 < calls["n"] <= (budget_attempts + 2) * 2

    def test_idle_timeout_hang_is_retryable_and_recovers(self, tmp_path, monkeypatch):
        """An SDK idle-timeout (hang) is treated as transient and the stage recovers."""
        self._no_backoff_sleep(monkeypatch)
        calls = {"n": 0}

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                # Mirror claude_cli's hang detector: a RuntimeError whose message
                # contains "idle timeout" (so retry.is_retryable matches).
                raise RuntimeError("SDK idle timeout: no event in 120.0s")
            (tmp_path / "clusters.json").write_text(
                json.dumps({"clusters": [{"story": "S", "article_ids": ["A1"]}]}), encoding="utf-8"
            )
            yield _result_event()

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)
        row = orchestrate.run_stage(
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

        def fake_stream(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("authentication failed")  # not in retryable patterns
            yield  # pragma: no cover

        monkeypatch.setattr(orchestrate.claude_cli, "stream_sync", fake_stream)
        with pytest.raises(RuntimeError, match="failed after retry"):
            orchestrate.run_stage(
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
