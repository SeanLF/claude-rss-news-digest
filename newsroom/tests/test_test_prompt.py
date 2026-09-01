"""Tests for test_prompt.py -- the prompt-eval harness.

Covers the two things repaired/added in this stream:

1. The Python-2 bare-except syntax bug in `list_runs()` (the file used to
   raise SyntaxError on import; `list_runs` itself must skip malformed run dirs
   without crashing).
2. The L1 grader wiring (`grade_run_selections`, `format_grade_report`,
   `cmd_grade`/`cmd_compare`) that lets a run's selections.json be scored and
   compared across model tiers.

No network, no real Claude calls, no real DB -- small fixtures and tmp dirs.
"""

import json
import sys
from pathlib import Path

import pytest

# Add src/ to path so we can import the harness module.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import test_prompt

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _article(headline="A short, clear headline about something", article_id="A1"):
    return {
        "headline": headline,
        "summary": "A concise summary of the story in a single readable sentence.",
        "why_it_matters": "Why this matters, kept brief and to the point.",
        "sources": [{"article_id": article_id}],
    }


def _good_selections():
    """A schema-valid, all-checks-pass selections payload (mirrors graders' fixture)."""
    return {
        "must_know": [_article(f"Must-know story number {i}") for i in range(2)],
        "should_know": [_article(f"Should-know story number {i}") for i in range(4)],
        "preheader": "A short preheader well under the 150 character cap.",
    }


# --------------------------------------------------------------------------- #
# The repaired list_runs() path (was a Python-2 bare-except SyntaxError)
# --------------------------------------------------------------------------- #


class TestListRunsSyntaxFix:
    def test_module_imports_cleanly(self):
        # If the bare-except were still present this import would have raised
        # SyntaxError at collection time; reaching here proves the fix.
        assert hasattr(test_prompt, "list_runs")

    def test_list_runs_skips_malformed_dirs(self, tmp_path, monkeypatch):
        """A run dir with invalid run.json must be skipped, not crash.

        This exercises the except (json.JSONDecodeError, KeyError,
        FileNotFoundError) clause that the Python-2 syntax used to break.
        """
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()

        # 1. Valid run.
        good = runs_dir / "2026-06-14_baseline_sonnet_120000"
        good.mkdir()
        (good / "run.json").write_text(
            json.dumps(
                {
                    "id": good.name,
                    "snapshot": "2026-06-14",
                    "prompt": "baseline",
                    "model": "sonnet",
                    "created_at": "2026-06-14T12:00:00+00:00",
                }
            )
        )

        # 2. Corrupt JSON -> JSONDecodeError.
        bad_json = runs_dir / "2026-06-14_baseline_haiku_130000"
        bad_json.mkdir()
        (bad_json / "run.json").write_text("{not valid json")

        # 3. Missing a required key -> KeyError in Run.from_dict.
        missing_key = runs_dir / "2026-06-14_baseline_opus_140000"
        missing_key.mkdir()
        (missing_key / "run.json").write_text(json.dumps({"id": "x"}))

        # 4. Missing run.json entirely -> FileNotFoundError.
        no_file = runs_dir / "2026-06-14_baseline_sonnet_150000"
        no_file.mkdir()

        monkeypatch.setattr(test_prompt, "RUNS_DIR", runs_dir)

        runs = test_prompt.list_runs()

        assert [r.id for r in runs] == [good.name]

    def test_list_runs_empty_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(test_prompt, "RUNS_DIR", tmp_path / "nonexistent")
        assert test_prompt.list_runs() == []


# --------------------------------------------------------------------------- #
# L1 grader wiring
# --------------------------------------------------------------------------- #


class TestGradeRunSelections:
    def test_good_selections_pass(self):
        grade = test_prompt.grade_run_selections(_good_selections(), use_recent=False)
        assert grade["passed"] is True
        assert grade["pass_rate"] == 1.0
        assert grade["n_failed"] == 0
        assert grade["n_checks"] > 0
        names = {c["name"] for c in grade["checks"]}
        assert "schema_valid" in names

    def test_broken_selections_fail_with_failures_listed(self):
        sel = _good_selections()
        sel["must_know"] = []  # must_know=0 falls below the [1,6] count range.
        grade = test_prompt.grade_run_selections(sel, use_recent=False)
        assert grade["passed"] is False
        assert grade["n_failed"] >= 1
        failed = {c["name"] for c in grade["checks"] if not c["passed"]}
        assert "story_counts_in_range" in failed

    def test_no_recent_skips_dedup_check(self):
        grade = test_prompt.grade_run_selections(_good_selections(), use_recent=False)
        dedup = next(c for c in grade["checks"] if c["name"] == "dedup_vs_recent")
        assert dedup["passed"] is True
        assert "skipped" in dedup["detail"]

    def test_use_recent_pulls_titles_and_flags_dupes(self, monkeypatch):
        sel = _good_selections()
        # Make one headline collide with a "recently shown" title.
        monkeypatch.setattr(test_prompt, "get_recent_rss_titles", lambda days=7: {"Must-know story number 0"})
        grade = test_prompt.grade_run_selections(sel, use_recent=True)
        dedup = next(c for c in grade["checks"] if c["name"] == "dedup_vs_recent")
        assert dedup["passed"] is False
        assert grade["passed"] is False

    def test_use_recent_empty_history_runs_check_not_skipped(self, monkeypatch):
        # Empty DB history must still RUN the dedup check (passing), not skip it.
        monkeypatch.setattr(test_prompt, "get_recent_rss_titles", lambda days=7: set())
        grade = test_prompt.grade_run_selections(_good_selections(), use_recent=True)
        dedup = next(c for c in grade["checks"] if c["name"] == "dedup_vs_recent")
        assert dedup["passed"] is True
        assert "skipped" not in dedup["detail"]


class TestGetRecentRssTitles:
    def test_returns_empty_when_db_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(test_prompt, "DB_PATH", tmp_path / "no.db")
        assert test_prompt.get_recent_rss_titles() == set()


class TestFormatGradeReport:
    def test_renders_pass_and_check_lines(self):
        grade = test_prompt.grade_run_selections(_good_selections(), use_recent=False)
        out = test_prompt.format_grade_report(grade, label="run-x")
        assert "L1 GRADE [run-x]" in out
        assert "PASS" in out
        assert "schema_valid" in out

    def test_renders_fail(self):
        sel = _good_selections()
        sel["preheader"] = "x" * 500  # over preheader cap
        grade = test_prompt.grade_run_selections(sel, use_recent=False)
        out = test_prompt.format_grade_report(grade)
        assert "FAIL" in out


# --------------------------------------------------------------------------- #
# CLI command wiring (grade / compare) via load_run round-trip
# --------------------------------------------------------------------------- #


def _write_run(runs_dir: Path, run_id: str, model: str, selections: dict) -> None:
    d = runs_dir / run_id
    d.mkdir()
    (d / "run.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "snapshot": "2026-06-14",
                "prompt": "baseline",
                "model": model,
                "created_at": "2026-06-14T12:00:00+00:00",
                "failed": False,
            }
        )
    )
    (d / "selections.json").write_text(json.dumps(selections))
    (d / "metrics.json").write_text(json.dumps({}))


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestCmdGrade:
    def test_grade_json_output(self, tmp_path, monkeypatch, capsys):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        _write_run(runs_dir, "2026-06-14_baseline_sonnet_120000", "sonnet", _good_selections())
        monkeypatch.setattr(test_prompt, "RUNS_DIR", runs_dir)

        test_prompt.cmd_grade(
            _Args(run="2026-06-14_baseline_sonnet_120000", format="json", no_recent=True, labels=None)
        )
        out = json.loads(capsys.readouterr().out)
        assert out["grade"]["passed"] is True
        assert out["run"] == "2026-06-14_baseline_sonnet_120000"

    def test_grade_missing_run_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(test_prompt, "RUNS_DIR", tmp_path / "runs")
        with pytest.raises(SystemExit):
            test_prompt.cmd_grade(_Args(run="nope", format="text", no_recent=True, labels=None))


class TestCmdCompare:
    def test_compare_json_two_models(self, tmp_path, monkeypatch, capsys):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        good = _good_selections()
        broken = _good_selections()
        broken["must_know"] = []  # fails story_counts_in_range
        _write_run(runs_dir, "2026-06-14_baseline_sonnet_120000", "sonnet", good)
        _write_run(runs_dir, "2026-06-14_baseline_haiku_130000", "haiku", broken)
        monkeypatch.setattr(test_prompt, "RUNS_DIR", runs_dir)

        test_prompt.cmd_compare(
            _Args(
                run_a="2026-06-14_baseline_sonnet_120000",
                run_b="2026-06-14_baseline_haiku_130000",
                format="json",
                no_recent=True,
            )
        )
        out = json.loads(capsys.readouterr().out)
        assert out["a"]["model"] == "sonnet"
        assert out["b"]["model"] == "haiku"
        assert out["a"]["grade"]["passed"] is True
        assert out["b"]["grade"]["passed"] is False

    def test_compare_text_marks_differing_checks(self, tmp_path, monkeypatch, capsys):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        good = _good_selections()
        broken = _good_selections()
        broken["must_know"] = []
        _write_run(runs_dir, "2026-06-14_baseline_sonnet_120000", "sonnet", good)
        _write_run(runs_dir, "2026-06-14_baseline_haiku_130000", "haiku", broken)
        monkeypatch.setattr(test_prompt, "RUNS_DIR", runs_dir)

        test_prompt.cmd_compare(
            _Args(
                run_a="2026-06-14_baseline_sonnet_120000",
                run_b="2026-06-14_baseline_haiku_130000",
                format="text",
                no_recent=True,
            )
        )
        out = capsys.readouterr().out
        assert "sonnet vs haiku" in out
        assert "differs" in out
        assert "story_counts_in_range" in out


class TestCreateSnapshot:
    def test_a_subdirectory_in_claude_input_does_not_break_the_snapshot(self, tmp_path, monkeypatch):
        """The per-story WRITE fan-out is the first thing to leave a DIRECTORY in
        claude_input (write_branches/), and it survives until the next full run wipes the
        tree. A flat copy2 over iterdir() raises IsADirectoryError on it, which breaks
        `make prompt` for everyone after any per-story run."""
        claude_input = tmp_path / "claude_input"
        claude_input.mkdir()
        (claude_input / "selected.json").write_text("{}")
        (claude_input / "selections.json").write_text("{}")
        (claude_input / "write_branches" / "s00").mkdir(parents=True)
        (claude_input / "write_branches" / "s00" / "selected.json").write_text("{}")
        monkeypatch.setattr(test_prompt, "CLAUDE_INPUT_DIR", claude_input)
        monkeypatch.setattr(test_prompt, "SNAPSHOTS_DIR", tmp_path / "snapshots")

        date_str = test_prompt.create_snapshot()

        snapshot = tmp_path / "snapshots" / date_str
        assert [p.name for p in snapshot.iterdir()] == ["selected.json"]
