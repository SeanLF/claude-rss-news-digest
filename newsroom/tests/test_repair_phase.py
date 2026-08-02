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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
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

    def test_logged_event_carries_run_id_and_timestamp(self, tmp_path, monkeypatch):
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
