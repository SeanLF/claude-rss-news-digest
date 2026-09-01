"""Tests for the repair-not-drop orchestration phase (orchestrate._run_repair_phase).

The Claude Agent SDK cannot run here (CLAUDECODE=1 blocks nested claude), so every
test mocks ``claude_cli.run_agent`` -- a fake that WRITES the output file the real
agent would (keyed off the substituted filename in the system prompt) and returns a
StageResult. These cover the deterministic glue: request skip, recheck-draft
construction, the repaired/recheck-fail/guard-fail ladders, and fail-soft. The real
two-model behaviour is validated later in Docker; there is no model call here.
"""

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
import repair
from claude_cli import StageResult


def _stage_result():
    return StageResult(
        subtype="success",
        text="",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        total_cost_usd=0.01,
        duration_ms=1000,
        is_error=False,
        api_error_status=None,
    )


class _FakeAgent:
    """A run_agent stand-in that writes the file the real agent would and counts calls.

    Distinguishes the two agents by the (substituted) output filename in the system
    prompt: the recheck runs coherence.md re-pointed at recheck_report.json; the
    repairer writes repaired_fields.json.
    """

    def __init__(self, claude_input_dir, *, repaired, recheck, repair_raises=False):
        self.dir = claude_input_dir
        self.repaired = repaired
        self.recheck = recheck
        self.repair_raises = repair_raises
        self.calls = []

    async def __call__(self, *_a, **k):
        body = k.get("system_prompt", "")
        if "recheck_report.json" in body:
            self.calls.append("recheck")
            (self.dir / "recheck_report.json").write_text(json.dumps(self.recheck))
        elif "repaired_fields.json" in body:
            self.calls.append("repair")
            if self.repair_raises:
                raise RuntimeError("repair agent boom")
            (self.dir / "repaired_fields.json").write_text(json.dumps(self.repaired))
        return _stage_result()


def _write_inputs(tmp_path, *, failed_fields=("headline",), article_id="A2"):
    draft = {
        "must_know": [
            {"headline": "good", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]},
            {"headline": "bad", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": article_id}]},
        ],
        "should_know": [],
        "preheader": "p",
    }
    coherence = {
        "results": [
            {"headline": "good", "article_ids": ["A1"], "pass": True},
            {
                "headline": "bad",
                "article_ids": [article_id],
                "pass": False,
                "reason": "headline: wrong entity",
                "failed_fields": list(failed_fields),
            },
        ]
    }
    (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
    (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))


def _run(tmp_path):
    return asyncio.run(orchestrate._run_repair_phase(tmp_path, model_override=None, cwd="."))


def _resolution(tmp_path):
    return json.loads((tmp_path / "repair_resolution.json").read_text())


class TestRepairPhase:
    def test_skips_when_no_repairable_failures(self, tmp_path, monkeypatch):
        # Only a passing story + a why-only failure -> no repair request, no agent call.
        draft = {
            "must_know": [{"headline": "ok", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}],
            "should_know": [],
            "preheader": "p",
        }
        coherence = {"results": [{"headline": "ok", "article_ids": ["A1"], "pass": True}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
        (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))
        fake = _FakeAgent(tmp_path, repaired={"results": []}, recheck={"results": []})
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        rows = _run(tmp_path)

        assert rows == []
        assert fake.calls == []
        assert not (tmp_path / "repair_resolution.json").exists()

    def test_happy_path_repairs_and_keeps_via_recheck(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, failed_fields=("headline",))
        fake = _FakeAgent(
            tmp_path,
            repaired={"results": [{"article_ids": ["A2"], "headline": "bad, corrected", "action": "corrected"}]},
            recheck={"results": [{"headline": "bad, corrected", "article_ids": ["A2"], "pass": True}]},
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        rows = _run(tmp_path)

        assert fake.calls == ["repair", "recheck"]
        assert len(rows) == 2  # a usage row per agent call
        res = _resolution(tmp_path)["results"][0]
        assert res["status"] == "repaired"
        assert res["patched_fields"] == {"headline": "bad, corrected"}
        # The recheck draft carries the PATCHED story, not the original.
        recheck_draft = json.loads((tmp_path / "recheck_draft.json").read_text())
        assert recheck_draft["must_know"][0]["headline"] == "bad, corrected"

    def test_logged_event_carries_run_id_process_and_timestamp(self, tmp_path, monkeypatch):
        # repair_log.jsonl is an APPEND-ONLY corpus spanning every run, but the events
        # carried no run or time field -- so reviewing week 1 in prod, 3 of 6 events
        # could not be attributed to a run at all once the rotated digest.log aged out.
        # Without these the log cannot be joined to digest_runs, sliced by date, or
        # used to compute a per-run repair rate.
        # The log lands in claude_input's PARENT, so this test nests claude_input one
        # level down -- otherwise it reads a log every other test in this class has
        # also appended to (they all share pytest's tmp_path.parent).
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        _write_inputs(claude_input, failed_fields=("headline",))
        fake = _FakeAgent(
            claude_input,
            repaired={"results": [{"article_ids": ["A2"], "headline": "bad, corrected", "action": "corrected"}]},
            recheck={"results": [{"headline": "bad, corrected", "article_ids": ["A2"], "pass": True}]},
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)
        monkeypatch.setattr(orchestrate.db, "current_run_id", lambda: 4242)

        _run(claude_input)

        event = json.loads((tmp_path / "repair_log.jsonl").read_text().splitlines()[0])
        assert event["run_id"] == 4242
        # The writer half of the --resume attribution. Without this stamp backfill_run_id
        # matches nothing and every resumed run's events stay null -- with the whole suite
        # green, because every other test of it hand-builds the events it reads.
        assert event["proc"] == repair.PROCESS_TOKEN
        # ISO-8601 UTC, sortable as a plain string.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00", event["ts"]), event["ts"]

    def test_recheck_failure_drops(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, failed_fields=("headline",))
        fake = _FakeAgent(
            tmp_path,
            repaired={"results": [{"article_ids": ["A2"], "headline": "bad, corrected", "action": "corrected"}]},
            recheck={
                "results": [
                    {"headline": "bad, corrected", "article_ids": ["A2"], "pass": False, "failed_fields": ["headline"]}
                ]
            },
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        _run(tmp_path)

        assert _resolution(tmp_path)["results"][0]["status"] == "recheck_failed"

    def test_guard_failure_skips_recheck(self, tmp_path, monkeypatch):
        # Repairer touched summary when only headline was flagged -> guard fail ->
        # nothing to re-check, so the recheck agent must NOT run.
        _write_inputs(tmp_path, failed_fields=("headline",))
        fake = _FakeAgent(
            tmp_path,
            repaired={"results": [{"article_ids": ["A2"], "summary": "sneaky", "action": "corrected"}]},
            recheck={"results": []},
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        _run(tmp_path)

        assert fake.calls == ["repair"]  # recheck never called
        assert _resolution(tmp_path)["results"][0]["status"] == "guard_failed"

    def test_best_effort_swallows_repair_agent_failure(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, failed_fields=("headline",))
        fake = _FakeAgent(tmp_path, repaired={"results": []}, recheck={"results": []}, repair_raises=True)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        rows = asyncio.run(orchestrate._run_repair_phase_best_effort(tmp_path, model_override=None, cwd="."))

        assert rows == []
        # No resolution written -> merge drops the flagged story exactly as today.
        assert not (tmp_path / "repair_resolution.json").exists()

    def test_stale_resolution_cleared_on_phase_failure(self, tmp_path, monkeypatch):
        # Same-day --resume reuses claude_input, so a PRIOR run's repair_resolution
        # (a "repaired" keep) can be on disk. If THIS run's repair phase fails, the
        # stale keep must be cleared -- else merge keeps a story this run never
        # confirmed (fail-toward-KEEP). The phase clears the file at entry.
        (tmp_path / "repair_resolution.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "article_ids": ["A2"],
                            "status": "repaired",
                            "patched_fields": {"headline": "stale keep"},
                            "recheck_pass": True,
                        }
                    ]
                }
            )
        )
        _write_inputs(tmp_path, failed_fields=("headline",))
        fake = _FakeAgent(tmp_path, repaired={"results": []}, recheck={"results": []}, repair_raises=True)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        asyncio.run(orchestrate._run_repair_phase_best_effort(tmp_path, model_override=None, cwd="."))

        assert not (tmp_path / "repair_resolution.json").exists()

    def test_stale_resolution_cleared_when_nothing_repairable(self, tmp_path, monkeypatch):
        # The no-op early return must also clear a stale resolution, not leave it.
        (tmp_path / "repair_resolution.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "article_ids": ["A2"],
                            "status": "repaired",
                            "patched_fields": {"headline": "stale keep"},
                            "recheck_pass": True,
                        }
                    ]
                }
            )
        )
        draft = {
            "must_know": [{"headline": "ok", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}],
            "should_know": [],
            "preheader": "p",
        }
        coherence = {"results": [{"headline": "ok", "article_ids": ["A1"], "pass": True}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
        (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))
        fake = _FakeAgent(tmp_path, repaired={"results": []}, recheck={"results": []})
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        rows = _run(tmp_path)

        assert rows == []
        assert not (tmp_path / "repair_resolution.json").exists()


class TestSpecDrift:
    """A drifted or missing coherence.md disables the whole repair path.

    ``_recheck_spec`` asserts the filenames it re-points are present so a drifted prompt
    "fails loudly here rather than silently re-checking against the wrong file" -- but the
    best-effort wrapper caught it broadly and logged one WARNING that read exactly like a
    repair phase that legitimately found nothing. A config error and a quiet no-op must not
    look alike.
    """

    REPO_ROOT = Path(__file__).parent.parent.parent

    def _agents_dir(self, tmp_path, *, coherence_body):
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "repair.md").write_text((self.REPO_ROOT / ".claude" / "agents" / "repair.md").read_text())
        (agents / "coherence.md").write_text(coherence_body)
        return agents

    def _drifted(self, tmp_path):
        real = (self.REPO_ROOT / ".claude" / "agents" / "coherence.md").read_text()
        # The exact drift the assertion exists for: the output filename was renamed in the
        # prompt, so the substitution would silently no-op and the re-check would read and
        # write the RUN's own files instead of the scoped ones.
        return self._agents_dir(tmp_path, coherence_body=real.replace("coherence_report.json", "audit_report.json"))

    def test_a_drifted_prompt_is_reported_as_a_fault_not_a_quiet_phase_failure(self, tmp_path, monkeypatch, caplog):
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        _write_inputs(claude_input, failed_fields=("headline",))
        fake = _FakeAgent(claude_input, repaired={"results": []}, recheck={"results": []})
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", self._drifted(tmp_path))

        with caplog.at_level("DEBUG"):
            rows = asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        # Still best-effort: the run is not aborted and merge drops exactly as before.
        assert rows == []
        assert not (claude_input / "repair_resolution.json").exists()
        # Detected BEFORE the repairer is paid for -- a broken config cannot be fixed by
        # spending on a repair whose re-check can never run.
        assert fake.calls == []
        # ERROR, not WARNING: a WARNING nobody greps for hid 18 days of a dead feed.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, f"spec drift logged below ERROR: {caplog.text!r}"
        # And in the one place the post-run invariants can see it.
        health = json.loads((claude_input / "repair_health.json").read_text())
        assert health["outcome"] == "spec_error"
        assert "coherence.md" in health["detail"]

    def test_a_missing_prompt_file_is_the_same_fault(self, tmp_path, monkeypatch):
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        _write_inputs(claude_input, failed_fields=("headline",))
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "repair.md").write_text((self.REPO_ROOT / ".claude" / "agents" / "repair.md").read_text())
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", agents)

        asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        assert json.loads((claude_input / "repair_health.json").read_text())["outcome"] == "spec_error"

    def test_a_phase_that_found_nothing_records_no_fault(self, tmp_path, monkeypatch, caplog):
        # The other half of the contract: a legitimate no-op must stay quiet, or the fault
        # signal is worthless.
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        draft = {
            "must_know": [{"headline": "ok", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}],
            "should_know": [],
            "preheader": "p",
        }
        (claude_input / "draft_selections.json").write_text(json.dumps(draft))
        (claude_input / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "ok", "article_ids": ["A1"], "pass": True}]})
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", _FakeAgent(claude_input, repaired={}, recheck={}))

        with caplog.at_level("DEBUG"):
            asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        assert not (claude_input / "repair_health.json").exists()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text

    def test_an_agent_failure_is_not_reported_as_a_spec_fault(self, tmp_path, monkeypatch):
        # A model call that failed is a repair that did not happen, not a broken config;
        # conflating them would make the fault signal fire on ordinary flakiness.
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        _write_inputs(claude_input, failed_fields=("headline",))
        fake = _FakeAgent(claude_input, repaired={"results": []}, recheck={"results": []}, repair_raises=True)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        assert not (claude_input / "repair_health.json").exists()

    def test_a_stale_fault_from_an_earlier_attempt_is_cleared(self, tmp_path, monkeypatch):
        # Same-day --resume reuses claude_input, so yesterday's fault file must not make a
        # healthy run alert.
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        (claude_input / "repair_health.json").write_text(json.dumps({"outcome": "spec_error", "detail": "stale"}))
        _write_inputs(claude_input, failed_fields=("headline",))
        fake = _FakeAgent(
            claude_input,
            repaired={"results": [{"article_ids": ["A2"], "headline": "bad, corrected", "action": "corrected"}]},
            recheck={"results": [{"headline": "bad, corrected", "article_ids": ["A2"], "pass": True}]},
        )
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)

        asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        assert not (claude_input / "repair_health.json").exists()

    def test_a_drifted_repairer_prompt_is_the_same_fault(self, tmp_path, monkeypatch):
        # repair.md has every property the fault class names -- deterministic, recurring,
        # disables the path outright -- and was the half left reading as a quiet no-op.
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        _write_inputs(claude_input, failed_fields=("headline",))
        agents = self._agents_dir(
            tmp_path, coherence_body=(self.REPO_ROOT / ".claude" / "agents" / "coherence.md").read_text()
        )
        (agents / "repair.md").write_text("no frontmatter here")
        fake = _FakeAgent(claude_input, repaired={"results": []}, recheck={"results": []})
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", agents)

        asyncio.run(orchestrate._run_repair_phase_best_effort(claude_input, model_override=None, cwd="."))

        assert fake.calls == []
        health = json.loads((claude_input / "repair_health.json").read_text())
        assert health["outcome"] == "spec_error"
        assert "repair.md" in health["detail"]

    def test_a_stale_fault_does_not_alert_after_repair_is_switched_off(self, tmp_path, monkeypatch):
        # A spec fault, then REPAIR_ENABLED=false, then a same-day --resume: claude_input is
        # reused, so a fault file cleared only INSIDE the phase would be archived again and
        # alert on a run that never ran repair at all.
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        (claude_input / "repair_health.json").write_text(json.dumps({"outcome": "spec_error", "detail": "stale"}))
        monkeypatch.setattr(orchestrate.config, "REPAIR_ENABLED", False)
        monkeypatch.setattr(orchestrate, "_STAGES", ())

        asyncio.run(orchestrate.orchestrate_selections(claude_input_dir=claude_input))

        assert not (claude_input / "repair_health.json").exists()
