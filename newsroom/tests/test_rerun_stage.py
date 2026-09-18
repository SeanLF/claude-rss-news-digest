"""Probe 0's re-run half: run one stage from a run's archived inputs and compare."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rerun_stage


def test_a_stage_gets_the_archived_files_its_prompt_names_and_the_article_csvs():
    body = "Read /app/data/claude_input/recent_rss_titles.csv then write /app/data/claude_input/recap.txt"
    archive = {
        "recent_rss_titles.csv": "a,b",
        "recap.txt": "the archived output",
        "articles_1.csv": "x",
        "articles_2.csv": "y",
        "selected.json": "{}",
    }
    inputs = rerun_stage.stage_inputs(body, archive, output_name="recap.txt")
    assert inputs == {"recent_rss_titles.csv": "a,b", "articles_1.csv": "x", "articles_2.csv": "y"}


def test_compare_reports_each_rep_against_the_archive_and_against_each_other():
    c = rerun_stage.compare("the same text", ["the same text", "something else entirely"])
    assert c["vs_archived"][0] == 1.0
    assert c["vs_archived"][1] < 0.6
    assert len(c["pairwise"]) == 1 and c["pairwise"][0] < 0.6


def test_rerun_keeps_every_rep_and_writes_a_summary(tmp_path, monkeypatch):
    archive = {"recent_rss_titles.csv": "t1\nt2", "recap.txt": "archived recap"}
    monkeypatch.setattr(rerun_stage.db, "get_run_artifacts", lambda run: archive)
    monkeypatch.setattr(rerun_stage, "_run_date", lambda run: None)
    monkeypatch.setattr(
        rerun_stage.eval_coherence,
        "load_agent_for_eval",
        lambda agent, fixtures, override=None, **_: ("m", f"body {fixtures}/", {"type": "disabled"}, ["Read"]),
    )
    n = {"i": 0}

    async def fake_run(label, out_path, model, body, thinking, tools):
        n["i"] += 1
        out_path.write_text(f"rep {n['i']} recap")

    monkeypatch.setattr(rerun_stage.eval_coherence, "run_agent_to_file", fake_run)

    summary = asyncio.run(rerun_stage.rerun(300, "recap", reps=2, work=tmp_path))

    kept = sorted(p.name for p in tmp_path.glob("recap.*.txt"))
    assert kept == ["recap.0.txt", "recap.1.txt"]
    assert (tmp_path / "recent_rss_titles.csv").read_text() == "t1\nt2"
    assert summary["reps"] == 2 and len(summary["vs_archived"]) == 2
    assert json.loads((tmp_path / "summary.json").read_text())["stage"] == "recap"
