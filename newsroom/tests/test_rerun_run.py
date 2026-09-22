import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rerun_run


def test_restore_writes_every_archived_input_and_never_a_stage_output(tmp_path):
    archive = {
        "articles_1.csv": "article_id,title\nA1,x\n",
        "sources.csv": "id,name\nreuters,Reuters\n",
        "weekly_recap.txt": "recap",
        "recent_rss_titles.csv": "t\n",
        "yesterday_headlines.txt": "",
        "recent_digest_headlines.txt": "",
        "clusters.json": '{"stale": true}',
        "selections.json": '{"stale": true}',
        "thinking_write_s00.txt": "reasoning",
    }
    written = rerun_run.restore_inputs(archive, tmp_path)
    assert (tmp_path / "articles_1.csv").exists() and (tmp_path / "sources.csv").exists()
    assert not (tmp_path / "clusters.json").exists() and not (tmp_path / "selections.json").exists()
    assert not (tmp_path / "thinking_write_s00.txt").exists()
    assert sorted(written) == [
        "articles_1.csv",
        "recent_digest_headlines.txt",
        "recent_rss_titles.csv",
        "sources.csv",
        "weekly_recap.txt",
        "yesterday_headlines.txt",
    ]


def test_every_archived_trace_artifact_is_a_stage_output_or_an_input():
    """The archive sweep (db._TRACE_ARTIFACTS) and STAGE_OUTPUTS must agree: a trace artifact
    that is neither listed here nor an input would be restored and read by a re-run stage."""
    import db

    inputs = {
        "sources.csv",
        "weekly_recap.txt",
        "recent_rss_titles.csv",
        "yesterday_headlines.txt",
        "recent_digest_headlines.txt",
        "article_index.json",
    }
    unclassified = set(db._TRACE_ARTIFACTS) - rerun_run.STAGE_OUTPUTS - inputs
    assert not unclassified, unclassified


def test_summary_carries_the_band_fields():
    reps = [{"cost_usd": 5.0, "wall_s": 1200.0, "stories": 16}, {"cost_usd": 5.5, "wall_s": 1300.0, "stories": 15}]
    s = rerun_run.summarise(300, reps)
    assert s == {"run": 300, "reps": 2, "cost_usd": [5.0, 5.5], "wall_s": [1200.0, 1300.0], "stories": [16, 15]}


def test_one_rep_drives_orchestrate_then_merge_and_sums_usage(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_orchestrate(*, claude_input_dir, on_usage, today, **_):
        calls.append("orchestrate")
        on_usage({"api_cost_usd": 1.25})
        on_usage({"api_cost_usd": 0.75})
        return []

    def fake_assemble(claude_input_dir):
        calls.append("merge")
        p = claude_input_dir / "selections.json"
        p.write_text(json.dumps({"must_know": [{"headline": "a"}], "should_know": [{"headline": "b"}]}))
        return p

    monkeypatch.setattr(rerun_run.orchestrate, "orchestrate_selections", fake_orchestrate)
    monkeypatch.setattr(rerun_run.merge, "assemble_selections", fake_assemble)

    r = asyncio.run(rerun_run._one_rep({"sources.csv": "id\n"}, tmp_path / "rep0", None))
    assert calls == ["orchestrate", "merge"]
    assert r["cost_usd"] == 2.0 and r["stories"] == 2 and r["wall_s"] >= 0


def test_rep_refuses_a_non_empty_input_dir(tmp_path, monkeypatch):
    """The prompts name /app/data/claude_input/ absolutely, so a rep owns that mount; a leftover
    file from another attempt would be read as this rep's stage output."""
    (tmp_path / "recap.txt").write_text("stale")
    monkeypatch.setattr(rerun_run, "_archive_for", lambda run: {"sources.csv": "id\n"})
    with pytest.raises(SystemExit, match="not empty"):
        asyncio.run(rerun_run.rep(300, input_dir=tmp_path))


def test_summarise_dir_collects_rep_results_in_order(tmp_path):
    for i, cost in ((1, 5.5), (0, 5.0)):
        d = tmp_path / f"rep{i}"
        d.mkdir()
        (d / rerun_run.REP_RESULT_NAME).write_text(json.dumps({"cost_usd": cost, "wall_s": 1.0, "stories": 3}))
    s = rerun_run.summarise_dir(300, tmp_path)
    assert s["cost_usd"] == [5.0, 5.5]
    assert json.loads((tmp_path / "summary.json").read_text())["reps"] == 2
