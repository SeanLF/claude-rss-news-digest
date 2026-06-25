"""Tests for the pure pieces of the RECAP A/B harness (prompt building, assembly).

The model calls themselves are integration (smoke-tested live in Docker); here we
only pin the deterministic helpers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import datetime as dt
import json

import pytest
from eval_recap_ab import (
    RECAP_SYSTEM_PROMPT,
    _load_existing,
    _save_cases,
    assemble_case,
    build_recap_user_prompt,
    generate_recap,
    select_pending_dates,
    summarize_ab,
)


def test_select_pending_dates_skips_already_completed_windows():
    dates = [dt.date(2026, 6, 24), dt.date(2026, 6, 14), dt.date(2026, 6, 4)]
    pending = select_pending_dates(dates, {"2026-06-14"})
    assert pending == [dt.date(2026, 6, 24), dt.date(2026, 6, 4)]


def test_user_prompt_lists_every_title_once():
    titles = ["Iran war halted by Senate", "EU-China trade clash", "Helicopter crash kills four"]
    prompt = build_recap_user_prompt(titles)
    for t in titles:
        assert t in prompt
    assert prompt.count("Helicopter crash kills four") == 1


def test_recap_system_prompt_forbids_headline_reproduction():
    # Faithful to recap.md: thematic language only, no reproduced titles.
    assert "thematic" in RECAP_SYSTEM_PROMPT.lower()
    assert "do not" in RECAP_SYSTEM_PROMPT.lower() or "don't" in RECAP_SYSTEM_PROMPT.lower()


def test_assemble_case_records_both_recaps_and_judge_verdicts():
    # Injected judge/grade keep this pure: no model calls.
    recaps = {"haiku": "Haiku recap text.", "sonnet": "Sonnet recap text."}

    def fake_judge(recap_text, titles):
        # Haiku "missed" a theme; Sonnet clean.
        return (["a missed theme"], []) if "Haiku" in recap_text else ([], [])

    def fake_grade(recap_text, source_titles):
        return {"passed": True}

    case = assemble_case(
        window_id="2026-03-16",
        end_date="2026-03-16",
        titles=["t1", "t2"],
        recaps=recaps,
        judge=fake_judge,
        grade=fake_grade,
    )

    assert case["window_id"] == "2026-03-16"
    assert case["models"]["haiku"]["missed_themes"] == ["a missed theme"]
    assert case["models"]["haiku"]["clean"] is False
    assert case["models"]["sonnet"]["clean"] is True
    assert case["models"]["sonnet"]["fabricated_themes"] == []


def test_summarize_ab_aggregates_per_model_and_paired_comparison():
    cases = [
        {
            "window_id": "w1",
            "models": {
                "haiku": {"missed_themes": ["a"], "fabricated_themes": [], "clean": False, "l1": {"passed": True}},
                "sonnet": {
                    "missed_themes": ["a", "b"],
                    "fabricated_themes": [],
                    "clean": False,
                    "l1": {"passed": False},
                },
            },
        },
        {
            "window_id": "w2",
            "models": {
                "haiku": {"missed_themes": [], "fabricated_themes": [], "clean": True, "l1": {"passed": True}},
                "sonnet": {"missed_themes": ["c"], "fabricated_themes": ["x"], "clean": False, "l1": {"passed": True}},
            },
        },
    ]

    s = summarize_ab(cases, models=["haiku", "sonnet"])

    assert s["haiku"]["total_missed"] == 1
    assert s["sonnet"]["total_missed"] == 3
    assert s["sonnet"]["total_fabricated"] == 1
    assert s["haiku"]["defect_rate"] == 0.5
    assert s["sonnet"]["defect_rate"] == 1.0
    assert s["haiku"]["l1_pass_rate"] == 1.0
    # Paired: haiku omitted strictly fewer themes in BOTH windows.
    assert s["_paired"]["haiku_fewer_missed"] == 2
    assert s["_paired"]["sonnet_fewer_missed"] == 0


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_generate_recap_rejects_empty_model_output(monkeypatch, blank):
    # An empty "successful" response must be a hard error, not a scored window:
    # otherwise the judge counts it as "missed everything" and biases the A/B.
    import claude_cli

    monkeypatch.setattr(claude_cli, "run_sync", lambda *a, **k: blank)
    with pytest.raises(RuntimeError, match="empty recap"):
        generate_recap(["t1", "t2"], "haiku")


def test_generate_recap_returns_stripped_text(monkeypatch):
    import claude_cli

    monkeypatch.setattr(claude_cli, "run_sync", lambda *a, **k: "  Real recap text.  ")
    assert generate_recap(["t1"], "haiku") == "Real recap text."


def test_load_existing_returns_empty_when_file_missing(tmp_path):
    assert _load_existing(tmp_path / "nope.json") == []


def test_load_existing_loads_saved_cases(tmp_path):
    p = tmp_path / "ab.json"
    _save_cases(p, [{"window_id": "w1"}])
    assert _load_existing(p) == [{"window_id": "w1"}]


def test_load_existing_raises_on_corrupt_file_instead_of_silent_restart(tmp_path):
    # A truncated/corrupt resume file must fail loudly, not silently discard
    # prior work and re-run every (expensive) window from scratch.
    p = tmp_path / "ab.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        _load_existing(p)


def test_save_cases_is_atomic_no_tmp_left_behind(tmp_path):
    p = tmp_path / "ab.json"
    _save_cases(p, [{"window_id": "w1"}])
    _save_cases(p, [{"window_id": "w1"}, {"window_id": "w2"}])
    assert json.loads(p.read_text())["cases"][-1]["window_id"] == "w2"
    assert list(tmp_path.glob("*.tmp")) == []
