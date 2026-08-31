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
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
from claude_cli import StageResult

REPO_ROOT = Path(__file__).parent.parent.parent
CLUSTER_SPEC = REPO_ROOT / ".claude" / "agents" / "cluster.md"
COHERENCE_SPEC = REPO_ROOT / ".claude" / "agents" / "coherence.md"


def _stage_result(
    *,
    text="",
    usage=None,
    cost=0.05,
    duration_ms=1000,
    subtype="success",
    is_error=False,
    api_error_status=None,
    files_read=(),
):
    """Build a StageResult like ``claude_cli.run_agent`` resolves to."""
    return StageResult(
        subtype=subtype,
        text=text,
        usage=usage
        or {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 8000,
        },
        total_cost_usd=cost,
        duration_ms=duration_ms,
        is_error=is_error,
        api_error_status=api_error_status,
        files_read=files_read,
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

    def test_real_specs_omit_effort(self):
        # Prod stays at the SDK default: no stage sets effort in frontmatter
        # (effort used to 400 on the Haiku RECAP stage -- no longer on 0.2.110,
        # see bin/sdk-canary -- and would silently change cost). Opt-in lever.
        assert orchestrate.parse_agent_spec(CLUSTER_SPEC).effort is None

    def test_parses_effort_and_thinking(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text(
            "---\nname: x\nmodel: claude-sonnet-5\ntools: Read, Write\neffort: medium\nthinking: adaptive\n---\nbody"
        )
        spec = orchestrate.parse_agent_spec(p)
        assert spec.effort == "medium"
        assert spec.thinking == {"type": "adaptive"}

    def test_coherence_enables_adaptive_thinking(self):
        """COHERENCE deliberately overrides orchestrate._THINKING, and the repair re-check
        inherits the override through this spec. `display` rides on the same config: it is
        billed identically either way, so omitting it only loses the trace."""
        spec = orchestrate.parse_agent_spec(COHERENCE_SPEC)
        assert spec.thinking == {"type": "adaptive", "display": "summarized"}

    def test_recheck_spec_inherits_coherence_thinking(self):
        """The repair re-check reuses the live checker prompt, so it must inherit
        the same thinking config -- otherwise the two halves of the same check
        run under different reasoning budgets."""
        spec = orchestrate.parse_agent_spec(COHERENCE_SPEC)
        assert orchestrate._recheck_spec(spec).thinking == spec.thinking

    def test_no_tuning_keys_default_none(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("---\nname: x\nmodel: claude-haiku-4-5\ntools: Read, Write\n---\nbody")
        spec = orchestrate.parse_agent_spec(p)
        assert spec.effort is None
        assert spec.thinking is None


# --------------------------------------------------------------------------- #
# render_body -- runtime context injection (current date, world-state grounding)
# --------------------------------------------------------------------------- #


class TestRenderBody:
    def test_substitutes_current_date_token(self):
        from datetime import date

        body = "Today is {{CURRENT_DATE}}. Write as of this date."
        out = orchestrate.render_body(body, today=date(2026, 7, 1))
        assert out == "Today is Wednesday, 1 July 2026. Write as of this date."
        assert "{{CURRENT_DATE}}" not in out

    def test_body_without_token_is_unchanged(self):
        from datetime import date

        body = "No token here."
        assert orchestrate.render_body(body, today=date(2026, 7, 1)) == body

    def test_defaults_to_utc_today(self):
        # Must anchor to UTC (the pipeline's canonical clock), not local time --
        # otherwise the WRITE "today" can disagree with the UTC digest date by a
        # full day near the UTC-midnight boundary.
        import datetime as dt

        utc_today = dt.datetime.now(dt.UTC).date()
        expected = f"{utc_today:%A}, {utc_today.day} {utc_today:%B} {utc_today.year}"
        out = orchestrate.render_body("d: {{CURRENT_DATE}}")
        assert out == f"d: {expected}"
        assert "{{CURRENT_DATE}}" not in out

    def test_invoke_agent_injects_date_into_system_prompt(self, monkeypatch):
        # The token in an agent body must be resolved BEFORE the body becomes the
        # SDK system_prompt -- otherwise WRITE reasons about "current" world state
        # with no date anchor and defaults to a stale training-data prior.
        seen = {}

        async def fake_run(*_a, **k):
            seen.update(k)
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        spec = orchestrate.AgentSpec(
            name="write",
            model="claude-sonnet-4-6",
            tools_str="Read, Write",
            body="Today is {{CURRENT_DATE}}.",
        )
        asyncio.run(orchestrate._invoke_agent(spec, model="claude-sonnet-4-6", cwd=None))
        from datetime import date

        assert "{{CURRENT_DATE}}" not in seen["system_prompt"]
        assert str(date.today().year) in seen["system_prompt"]


class TestWriteSpecGroundsWorldState:
    """The WRITE prompt must anchor 'current' to the run date and forbid asserting
    office-holders / political framing from prior knowledge (the stale-Biden bug)."""

    def test_write_spec_has_date_token_and_office_rule(self):
        spec = orchestrate.parse_agent_spec(REPO_ROOT / ".claude" / "agents" / "write.md")
        assert "{{CURRENT_DATE}}" in spec.body
        low = spec.body.lower()
        # names the failure mode explicitly (office-holder / administration prior)
        assert "administration" in low or "office-holder" in low or "in power" in low


class TestWriteSpecFulltext:
    """The WRITE prompt must read article_fulltext.json (if present) and treat it as the SAME
    article as the CSV row -- richer facts, not a new uncited source -- per fulltext.py (Task 2)."""

    def setup_method(self):
        self.spec = orchestrate.parse_agent_spec(REPO_ROOT / ".claude" / "agents" / "write.md")

    def test_reads_article_fulltext_optionally(self):
        low = self.spec.body.lower()
        assert "article_fulltext.json" in low
        # Same "if it exists -- skip if not found" phrasing as weekly_recap.txt's optional read.
        assert "if it exists -- skip if not found" in low

    def test_fabrication_rule_covers_full_text_not_just_summaries(self):
        low = self.spec.body.lower()
        assert "do not fabricate beyond what is in the article summaries or the article full text" in low

    def test_citation_rule_allows_support_from_full_text(self):
        low = self.spec.body.lower()
        assert "support may come from that article's csv summary or its full text" in low


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
    def test_logs_the_input_files_the_stage_actually_read(self, tmp_path, monkeypatch, caplog):
        # A stage is handed input files; until now nothing recorded whether it
        # opened them. Names go in the completion line so a run's log answers
        # "did WRITE ever read recent_digest_headlines.txt" without a replay.
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        reads = ("/w/claude_input/articles_1.csv", "/w/claude_input/recent_digest_headlines.txt")
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", _async_return(_stage_result(files_read=reads)))

        with caplog.at_level("INFO"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=out,
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )

        assert "articles_1.csv" in caplog.text
        assert "recent_digest_headlines.txt" in caplog.text

    def test_stage_that_opened_nothing_says_so_explicitly(self, tmp_path, monkeypatch, caplog):
        # A bare "read=" with nothing after it is exactly what a tired reader skims
        # past, and this is the case worth catching: a stage that produced valid
        # output without ever opening its input is guessing from the prompt alone.
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", _async_return(_stage_result(files_read=())))

        with caplog.at_level("INFO"):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=out,
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )

        assert "read=NOTHING" in caplog.text

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
        # Per-stage wall-clock latency is persisted for monitoring (was logged then discarded).
        assert row["duration_ms"] == 1000  # _stage_result default

    def test_passes_spec_effort_and_thinking(self, tmp_path, monkeypatch):
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        seen = {}

        async def fake_run(*_a, **k):
            seen.update(k)
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        spec = orchestrate.AgentSpec(
            name="cluster",
            model="claude-sonnet-5",
            tools_str="Read, Write",
            body="x",
            effort="medium",
            thinking={"type": "adaptive"},
        )
        _run_stage(
            spec,
            label="cluster",
            output_path=out,
            validate=_ok_validator,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert seen["effort"] == "medium"
        assert seen["thinking"] == {"type": "adaptive"}

    def test_defaults_to_disabled_thinking_no_effort(self, tmp_path, monkeypatch):
        # A spec with no tuning keys must reproduce the proven-good prod behaviour:
        # thinking disabled, effort unset (SDK default).
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        seen = {}

        async def fake_run(*_a, **k):
            seen.update(k)
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        _run_stage(
            _spec(),
            label="cluster",
            output_path=out,
            validate=_ok_validator,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert seen["thinking"] == orchestrate._THINKING == {"type": "disabled"}
        assert seen["effort"] is None

    def test_recorded_config_is_the_config_that_was_sent(self, tmp_path, monkeypatch):
        """A non-default spec must reach BOTH the SDK call and the usage row. A record site
        hardcoded to the module default is the drift the columns exist to catch; this fails
        against it. It cannot detect two identical inline copies -- that is structural, which is
        why `_resolved_thinking` is the single source."""
        out = tmp_path / "clusters.json"
        out.write_text(json.dumps({"clusters": [{"story": "x", "article_ids": ["A1"]}]}))
        seen = {}

        async def fake_run(*_a, **k):
            seen.update(k)
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        spec = _spec()
        object.__setattr__(spec, "thinking", {"type": "adaptive"})
        row = _run_stage(
            spec,
            label="cluster",
            output_path=out,
            validate=_ok_validator,
            model_override=None,
            cwd=None,
            claude_input_dir=tmp_path,
        )
        assert seen["thinking"] == {"type": "adaptive"}
        assert row["thinking"] == "adaptive", "recorded config disagrees with what was sent"

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

    def test_api_error_with_success_subtype_raises_retryable(self, monkeypatch):
        # The SDK trap: subtype="success" but is_error=True with api_error_status.
        # _invoke_agent must raise, AND the message must carry the status so
        # retry.is_retryable treats it as a transient API failure (not a hard fail).
        import retry

        monkeypatch.setattr(
            orchestrate.claude_cli,
            "run_agent",
            _async_return(_stage_result(subtype="success", is_error=True, api_error_status=529)),
        )
        with pytest.raises(RuntimeError) as exc:
            asyncio.run(orchestrate._invoke_agent(_spec(), model="m", cwd=None))
        assert "529" in str(exc.value)
        assert retry.is_retryable(exc.value)

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


class TestValidateCoherenceStrictStructure:
    """Fail-closed structure gate for coherence_report.json. A truncated-but-
    valid-JSON report used to only warn downstream (merge.py) and ship the
    unchecked tail; raising here instead makes the stage retry (see run_stage)."""

    def test_non_dict_entry_raises(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": ["not a dict"]}))
        with pytest.raises(ValueError, match="not an object"):
            orchestrate.validate_coherence(tmp_path)

    def test_string_pass_raises(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": [{"headline": "h", "pass": "false"}]}))
        with pytest.raises(ValueError, match="boolean 'pass'"):
            orchestrate.validate_coherence(tmp_path)

    def test_missing_pass_raises(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": [{"headline": "h"}]}))
        with pytest.raises(ValueError, match="boolean 'pass'"):
            orchestrate.validate_coherence(tmp_path)

    def test_undercoverage_vs_draft_raises(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(
            json.dumps(
                {
                    "must_know": [{"headline": "a"}],
                    "should_know": [{"headline": "b"}],
                    "preheader": "p",
                }
            )
        )
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "a", "pass": True}]})  # only 1 of 2 draft stories
        )
        with pytest.raises(ValueError, match="no result matches"):
            orchestrate.validate_coherence(tmp_path)

    def test_duplicate_results_cannot_mask_an_unchecked_story(self, tmp_path):
        # Identity-based coverage: two results for story "a" satisfy a COUNT
        # check while "b" goes unchecked -- that must still raise (a count-only
        # gate was defeatable; the unchecked story would then ship fail-open
        # via merge.py's keep-with-warning fallback).
        (tmp_path / "draft_selections.json").write_text(
            json.dumps(
                {
                    "must_know": [{"headline": "a"}],
                    "should_know": [{"headline": "b"}],
                    "preheader": "p",
                }
            )
        )
        (tmp_path / "coherence_report.json").write_text(
            json.dumps(
                {
                    "results": [
                        {"headline": "a", "pass": True},
                        {"headline": "a", "pass": True},
                    ]
                }
            )
        )
        with pytest.raises(ValueError, match=r"no result matches.*'b'"):
            orchestrate.validate_coherence(tmp_path)

    def test_retyped_headline_matches_via_article_ids(self, tmp_path):
        # COHERENCE re-types headlines; a drifted headline with intact cited
        # article_ids must still count as coverage (merge.py matches by ids
        # first -- validation must agree with assembly).
        (tmp_path / "draft_selections.json").write_text(
            json.dumps(
                {
                    "must_know": [{"headline": "a - original", "sources": [{"article_id": "A1"}]}],
                    "should_know": [],
                    "preheader": "p",
                }
            )
        )
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "A -- Original (retyped)", "article_ids": ["A1"], "pass": True}]})
        )
        orchestrate.validate_coherence(tmp_path)  # no raise

    def test_exact_coverage_passes(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(
            json.dumps(
                {
                    "must_know": [{"headline": "a"}],
                    "should_know": [{"headline": "b"}],
                    "preheader": "p",
                }
            )
        )
        (tmp_path / "coherence_report.json").write_text(
            json.dumps(
                {
                    "results": [
                        {"headline": "a", "pass": True},
                        {"headline": "b", "pass": False},
                    ]
                }
            )
        )
        orchestrate.validate_coherence(tmp_path)  # no raise

    def test_missing_draft_selections_skips_coverage_check(self, tmp_path, caplog):
        # draft_selections.json absent here -- the write-stage validator owns
        # that file, so validate_coherence must not raise over its absence.
        # But the skip must be VISIBLE: the coverage gate silently disabling
        # itself is exactly the wrong-file case most worth surfacing.
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": []}))
        with caplog.at_level("WARNING"):
            orchestrate.validate_coherence(tmp_path)  # no raise
        assert "skipping coherence coverage check" in caplog.text

    def test_unreadable_draft_selections_skips_coverage_check_with_warning(self, tmp_path, caplog):
        (tmp_path / "coherence_report.json").write_text(json.dumps({"results": []}))
        (tmp_path / "draft_selections.json").write_text("{not json")
        with caplog.at_level("WARNING"):
            orchestrate.validate_coherence(tmp_path)  # no raise
        assert "skipping coherence coverage check" in caplog.text


class TestValidateCoherenceFailedFields:
    """failed_fields is OPTIONAL on a pass:false entry -- graceful degradation
    for why_it_matters-only coherence failures (merge.py owns the field-aware
    drop/strip decision). This gate only enforces the wire shape: when present,
    a list of strings; absent is fine. Unknown field names inside the list are
    NOT rejected here (forward-compat) -- merge.py treats unknown names
    conservatively (full drop)."""

    def test_absent_failed_fields_on_fail_passes(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "h", "pass": False, "reason": "r"}]})
        )
        orchestrate.validate_coherence(tmp_path)  # no raise

    def test_valid_failed_fields_list_passes(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(
            json.dumps(
                {"results": [{"headline": "h", "pass": False, "reason": "r", "failed_fields": ["why_it_matters"]}]}
            )
        )
        orchestrate.validate_coherence(tmp_path)  # no raise

    def test_unknown_field_name_in_list_still_passes(self, tmp_path):
        # Forward-compat: unknown names are not rejected at this layer.
        (tmp_path / "coherence_report.json").write_text(
            json.dumps(
                {"results": [{"headline": "h", "pass": False, "reason": "r", "failed_fields": ["mystery_field"]}]}
            )
        )
        orchestrate.validate_coherence(tmp_path)  # no raise

    def test_non_list_failed_fields_raises(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(
            json.dumps(
                {"results": [{"headline": "h", "pass": False, "reason": "r", "failed_fields": "why_it_matters"}]}
            )
        )
        with pytest.raises(ValueError, match="failed_fields"):
            orchestrate.validate_coherence(tmp_path)

    def test_list_with_non_string_entries_raises(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "h", "pass": False, "reason": "r", "failed_fields": ["summary", 5]}]})
        )
        with pytest.raises(ValueError, match="failed_fields"):
            orchestrate.validate_coherence(tmp_path)

    def test_failed_fields_on_passing_entry_ignored(self, tmp_path):
        # Spec scopes the check to pass:false entries; a pass:true entry with a
        # malformed failed_fields should not be rejected by this gate.
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "h", "pass": True, "reason": "ok", "failed_fields": "oops"}]})
        )
        orchestrate.validate_coherence(tmp_path)  # no raise


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

    # CLUSTER is now the extract→join stage (not an agent): it reads articles_*.csv and calls
    # run_agent for per-article EXTRACTION, then joins deterministically and writes clusters.json
    # itself. So the fake must (a) have articles present and (b) return tag JSON for the
    # extraction call (system prompt "You extract clustering metadata").
    @staticmethod
    def _write_articles(claude_input_dir):
        claude_input_dir.joinpath("articles_1.csv").write_text(
            "article_id,source_id,title,summary\n"
            "A1,src1,Iran attacks cargo ship in Hormuz,Body\n"
            "A2,src2,Venezuela earthquake toll rises,Body\n"
        )

    @staticmethod
    def _extract_response():
        return _stage_result(
            text=json.dumps(
                {
                    "items": [
                        {
                            "article_id": "A1",
                            "entities": ["Iran"],
                            "keywords": ["ship"],
                            "primary_event": "iran hormuz attack",
                        },
                        {
                            "article_id": "A2",
                            "entities": ["Venezuela"],
                            "keywords": ["quake"],
                            "primary_event": "venezuela earthquake",
                        },
                    ]
                }
            )
        )

    def _fake_writer(self, claude_input_dir):
        """Return a run_agent stand-in that serves extraction + writes each stage's output.

        Identifies the running agent by a unique phrase in its system prompt: the extraction
        call ("extract clustering metadata") gets tag JSON (extract→join writes clusters.json
        itself); the remaining agent stages write their output file.
        """

        async def fake_run(_prompt, *, system_prompt, **_k):
            if "extract clustering metadata" in system_prompt:
                return self._extract_response()
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
        self._write_articles(tmp_path)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", self._fake_writer(tmp_path))
        # Point the orchestrator's spec loader at the real agent files.
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        rows = _orchestrate(claude_input_dir=tmp_path)

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]
        assert all(r["api_cost_usd"] >= 0 for r in rows)

    def test_raises_if_a_stage_never_validates(self, tmp_path, monkeypatch):
        # cluster (extract→join) produces a valid file, but the recap agent writes nothing ->
        # recap fails after its retry and the run aborts.
        self._write_articles(tmp_path)

        async def fake_run(_prompt, *, system_prompt, **_k):
            if "extract clustering metadata" in system_prompt:
                return self._extract_response()
            # recap (and later) agents intentionally write nothing.
            return _stage_result()

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        with pytest.raises(RuntimeError, match="recap stage failed"):
            _orchestrate(claude_input_dir=tmp_path)


# --------------------------------------------------------------------------- #
# fulltext wiring: the best-effort fetch step between SELECT and WRITE.
# fulltext.fetch_for_selected itself is unit-tested in test_fulltext.py (no
# network); these tests only cover the orchestrator's INTEGRATION with it --
# ordering and fault isolation.
# --------------------------------------------------------------------------- #


class TestFulltextWiring:
    def _fake_run_recording(self, claude_input_dir, calls):
        """Like TestOrchestrateSelections._fake_writer, but also appends to ``calls`` when the
        select/write agents run, so tests can assert fulltext's position between them."""
        base = TestOrchestrateSelections()._fake_writer(claude_input_dir)

        async def fake_run(_prompt, *, system_prompt, **_k):
            if "news editor" in system_prompt:
                calls.append("select")
            if "news writer" in system_prompt:
                calls.append("write")
            return await base(_prompt, system_prompt=system_prompt, **_k)

        return fake_run

    def test_fulltext_runs_between_select_and_write(self, tmp_path, monkeypatch):
        TestOrchestrateSelections._write_articles(tmp_path)
        calls: list[str] = []

        def fake_fetch(claude_input_dir):
            assert claude_input_dir == tmp_path
            calls.append("fulltext")
            return None

        monkeypatch.setattr(orchestrate.fulltext, "fetch_for_selected", fake_fetch)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", self._fake_run_recording(tmp_path, calls))
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        rows = _orchestrate(claude_input_dir=tmp_path)

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]
        assert calls.index("select") < calls.index("fulltext") < calls.index("write")

    def test_fulltext_raising_unexpectedly_does_not_abort_orchestration(self, tmp_path, monkeypatch, caplog):
        TestOrchestrateSelections._write_articles(tmp_path)

        def fake_fetch(_claude_input_dir):
            raise RuntimeError("totally unexpected bug inside fulltext")

        monkeypatch.setattr(orchestrate.fulltext, "fetch_for_selected", fake_fetch)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", TestOrchestrateSelections()._fake_writer(tmp_path))
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        with caplog.at_level("WARNING"):
            rows = _orchestrate(claude_input_dir=tmp_path)  # must not raise

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        # The unexpected-exception path must log with both the exception type/message and a
        # real traceback attached (exc_info=True), not just a bare str(e).
        assert any(
            "RuntimeError" in r.getMessage() and "totally unexpected bug inside fulltext" in r.getMessage()
            for r in warnings
        )
        assert any(r.exc_info is not None for r in warnings)

    def test_fulltext_disabled_skips_the_call_entirely(self, tmp_path, monkeypatch):
        TestOrchestrateSelections._write_articles(tmp_path)
        monkeypatch.setattr(orchestrate.config, "FULLTEXT_ENABLED", False)

        def fake_fetch(_claude_input_dir):
            raise AssertionError("fetch_for_selected must not be called when FULLTEXT_ENABLED=false")

        monkeypatch.setattr(orchestrate.fulltext, "fetch_for_selected", fake_fetch)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", TestOrchestrateSelections()._fake_writer(tmp_path))
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        rows = _orchestrate(claude_input_dir=tmp_path)  # must not raise (and must not call fake_fetch)

        assert [r["subagent"] for r in rows] == ["cluster", "recap", "select", "write", "coherence"]


# --------------------------------------------------------------------------- #
# Chaos / fault injection: prove the transient-overload reliability is real,
# not just asserted. We inject 529s into the agent invocation and verify the
# stage recovers (bounded backoff) or fails LOUD (-> run aborts -> alert fires).
# --------------------------------------------------------------------------- #


class TestThinkingDisplay:
    """thinking.display defaults to "omitted" on Sonnet 5 (it was "summarized" on 4.6), so
    turning adaptive on cost the reasoning trace -- ~27k tokens/run leaving no record. display
    is billed identically either way, so the trace is free to keep."""

    def _spec_file(self, tmp_path, extra):
        f = tmp_path / "a.md"
        f.write_text(f"---\nname: a\nmodel: claude-sonnet-5\ntools: Read\n{extra}\n---\nbody\n")
        return f

    def test_display_is_carried_into_the_thinking_config(self, tmp_path):
        spec = orchestrate.parse_agent_spec(self._spec_file(tmp_path, "thinking: adaptive\ndisplay: summarized"))
        assert spec.thinking == {"type": "adaptive", "display": "summarized"}

    def test_display_without_thinking_is_ignored(self, tmp_path):
        """display is a key ON the thinking config; with no thinking set there is nothing to
        attach it to, and inventing {"display": ...} alone would 400."""
        spec = orchestrate.parse_agent_spec(self._spec_file(tmp_path, "display: summarized"))
        assert spec.thinking is None

    def test_disabled_thinking_never_carries_display(self, tmp_path):
        """ThinkingConfigDisabled has no optional display key -- sending one is a 400."""
        spec = orchestrate.parse_agent_spec(self._spec_file(tmp_path, "thinking: disabled\ndisplay: summarized"))
        assert spec.thinking == {"type": "disabled"}


class TestStageAttemptIsBounded:
    """with_retry_async's deadline is only consulted after fn() RAISES. A stage that streams
    events forever never raises, so the 4h budget was never reached and only the container's
    5h SIGTERM stopped it. This is the audit's strongest argument for a durable executor --
    closed here in place."""

    def test_an_attempt_that_never_returns_is_cut_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(orchestrate, "_STAGE_ATTEMPT_TIMEOUT_S", 0.3)

        async def never_returns(*_a, **_k):
            await asyncio.sleep(30)

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", never_returns)
        started = time.monotonic()
        with pytest.raises((RuntimeError, TimeoutError, asyncio.TimeoutError)):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"attempt was not bounded: {elapsed:.1f}s"

    def test_a_run_deadline_caps_the_stage_budget(self, tmp_path, monkeypatch):
        """Each stage took a FRESH 4h budget, so seven stages could ask for 28h under a 5h
        systemd TimeoutStartSec. A run-level deadline has to win when it is sooner."""
        run_deadline = time.monotonic() + 1.0
        seen = {}

        async def fake_run(*_a, **_k):
            raise RuntimeError("overloaded")

        def capture(fn, *, label, deadline):
            seen["deadline"] = deadline
            raise RuntimeError("stop")

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        monkeypatch.setattr(orchestrate, "with_retry_async", capture)
        with pytest.raises((RuntimeError, TimeoutError, asyncio.TimeoutError)):
            _run_stage(
                _spec(),
                label="cluster",
                output_path=tmp_path / "clusters.json",
                validate=_ok_validator,
                model_override=None,
                cwd=None,
                claude_input_dir=tmp_path,
                run_deadline=run_deadline,
            )
        assert seen["deadline"] <= run_deadline + 0.01, "the per-stage budget outran the run deadline"


class TestUsageSurvivesALaterStageFailing:
    """A stage raising used to discard every earlier stage's usage row, because rows were
    returned in one batch at the end. The spend was already billed; only the record was lost --
    and on the --resume that follows, those stages are skipped and contribute nothing, so the
    cost is recorded nowhere at all."""

    def test_rows_for_completed_stages_are_emitted_before_the_failure(self, tmp_path, monkeypatch):
        TestOrchestrateSelections()._write_articles(tmp_path)
        writer = TestOrchestrateSelections()._fake_writer(tmp_path)
        recorded: list[dict] = []

        async def fake_run(_prompt, *, system_prompt, **k):
            # cluster/recap/select behave; WRITE explodes after they have all been billed.
            if "preheader" in system_prompt.lower():  # unique to write.md
                raise RuntimeError("write stage exploded")
            return await writer(_prompt, system_prompt=system_prompt, **k)

        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake_run)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", REPO_ROOT / ".claude" / "agents")

        with pytest.raises(RuntimeError):
            _orchestrate(claude_input_dir=tmp_path, on_usage=recorded.append)

        assert [r["subagent"] for r in recorded] == ["cluster", "recap", "select"]
        assert all("api_cost_usd" in r for r in recorded)


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
