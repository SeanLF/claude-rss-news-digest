"""The cohesion gate: one judgement over the selected clusters, applied fail-open."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import cohesion
from claude_cli import StageResult

ARTS = {
    "A1": {"title": "Trump may reveal secret AI rules", "summary": "Ars on the lawsuit"},
    "A2": {"title": "A look at Trump's new helipad", "summary": "BBC"},
    "A3": {"title": "Opinion: Defunding the military", "summary": "WSJ"},
}


def test_prompt_lists_ids_titles_and_snippets_and_nothing_else():
    text = cohesion.build_judge_prompt([{"group": 0, "article_ids": ["A1", "A2"]}], ARTS)
    assert "GROUP 0" in text and "A1" in text and "helipad" in text
    assert "http" not in text and "bbc_world" not in text


def test_parse_reads_events_per_group_and_tolerates_fences():
    text = 'ok\n```json\n{"results": [{"group": 0, "events": [["A1"], ["A2", "A3"]]}]}\n```'
    assert cohesion.parse_verdicts(text) == {0: [["A1"], ["A2", "A3"]]}
    assert cohesion.parse_verdicts("no json") == {}


def test_partition_must_cover_exactly_and_orders_largest_first():
    assert cohesion.validate_partition(["A1", "A2", "A3"], [["A1"], ["A2", "A3"]]) == [["A2", "A3"], ["A1"]]
    assert cohesion.validate_partition(["A1", "A2", "A3"], [["A1"], ["A2"]]) is None  # missing
    assert cohesion.validate_partition(["A1", "A2"], [["A1"], ["A2", "A1"]]) is None  # duplicate
    assert cohesion.validate_partition(["A1", "A2"], [["A1"], ["A2", "A9"]]) is None  # unknown


def test_verdict_applies_dominant_and_records_strays():
    selected = {"must_know": [], "should_know": [{"cluster_index": 33, "article_ids": ["A1", "A2", "A3"]}]}
    clusters = [{"story": "x", "article_ids": []}] * 33 + [{"story": "ai rules", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 33, "article_ids": ["A1", "A2", "A3"]}]
    doc = cohesion.judge_selected(selected, clusters, {0: [["A1"], ["A2"], ["A3"]]}, groups)
    v = doc["verdicts"][0]
    assert v["applied"] is True and v["dominant"] == ["A1"] and sorted(v["strays"]) == ["A2", "A3"]
    assert doc["split"] == 1 and doc["strays_removed"] == 2


def test_ties_go_to_the_group_holding_selects_first_citation():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A3", "A1"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}]
    doc = cohesion.judge_selected(selected, clusters, {0: [["A1"], ["A2"], ["A3"]]}, groups)
    assert doc["verdicts"][0]["dominant"] == ["A3"]


def test_one_event_and_bad_partitions_are_not_applied():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2"]}]
    one = cohesion.judge_selected(selected, clusters, {0: [["A1", "A2"]]}, groups)["verdicts"][0]
    assert one["applied"] is False and one["reason"] == "one event"
    bad = cohesion.judge_selected(selected, clusters, {0: [["A1"]]}, groups)["verdicts"][0]
    assert bad["applied"] is False and bad["reason"] == "not a partition"
    gone = cohesion.judge_selected(selected, clusters, {0: [["A2"], ["A1"]]}, groups)["verdicts"][0]
    # A1 is SELECT's only citation and lands alone; the tie rule keeps it dominant, so this applies.
    assert gone["applied"] is True and gone["dominant"] == ["A1"]


def test_a_dominant_that_drops_every_cited_id_is_refused():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A3"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}]
    v = cohesion.judge_selected(selected, clusters, {0: [["A1", "A2"], ["A3"]]}, groups)["verdicts"][0]
    assert v["applied"] is False and v["reason"] == "dominant drops every cited id"


def _stage_dir(tmp_path):
    (tmp_path / "selected.json").write_text(
        json.dumps(
            {
                "must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}],
                "should_know": [],
                "not_covered_blurb": "",
            }
        )
    )
    (tmp_path / "clusters.json").write_text(
        json.dumps({"clusters": [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]})
    )
    (tmp_path / "articles_1.csv").write_text(
        "article_id,source_id,title,published,summary\nA1,s,Trump may reveal secret AI rules,2026,ars\n"
        "A2,s,A look at Trump's new helipad,2026,bbc\nA3,s,Opinion: Defunding the military,2026,wsj\n"
    )
    return tmp_path


def test_stage_writes_the_artifact_and_a_usage_row(tmp_path, monkeypatch):
    async def fake(prompt, **kw):
        assert kw["tools"] == [] and kw["max_turns"] == 1
        return StageResult(
            subtype="success",
            text=json.dumps({"results": [{"group": 0, "events": [["A1"], ["A2"], ["A3"]]}]}),
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            total_cost_usd=0.01,
            duration_ms=10,
            is_error=False,
        )

    monkeypatch.setattr(cohesion.claude_cli, "run_agent", fake)
    row = asyncio.run(cohesion.run_cohesion_stage(_stage_dir(tmp_path), model="claude-sonnet-4-6", cwd=None))
    doc = json.loads((tmp_path / cohesion.COHESION_ARTIFACT).read_text())
    assert doc["outcome"] == "completed" and doc["verdicts"][0]["applied"] is True
    assert row["subagent"] == "cohesion"


def test_stage_fails_open(tmp_path, monkeypatch):
    async def boom(prompt, **kw):
        raise RuntimeError("529")

    monkeypatch.setattr(cohesion.claude_cli, "run_agent", boom)
    row = asyncio.run(cohesion.run_cohesion_stage(_stage_dir(tmp_path), model="claude-sonnet-4-6", cwd=None))
    doc = json.loads((tmp_path / cohesion.COHESION_ARTIFACT).read_text())
    assert doc["outcome"] == "failed" and not any(v["applied"] for v in doc["verdicts"])
    assert "529" in doc["reason"]
    assert row["subagent"] == "cohesion"


def test_two_stories_on_one_cluster_each_keep_their_own_citations():
    """Never assume cluster_index uniqueness (the 2026-07-16 duplicate-label incident). Each
    group's tie-break and drop-guard read the citations of ITS story, not the first story
    that happened to share the cluster."""
    selected = {
        "must_know": [{"cluster_index": 0, "article_ids": ["A1"]}],
        "should_know": [{"cluster_index": 0, "article_ids": ["A3"]}],
    }
    clusters = [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]
    groups = cohesion.selected_groups(selected, clusters)
    assert [g["group"] for g in groups] == [0, 1]
    verdict = {0: [["A1"], ["A2"], ["A3"]], 1: [["A1"], ["A2"], ["A3"]]}
    doc = cohesion.judge_selected(selected, clusters, verdict, groups)
    assert doc["verdicts"][0]["dominant"] == ["A1"]
    assert doc["verdicts"][1]["dominant"] == ["A3"]


def test_one_failed_batch_does_not_discard_the_others(tmp_path, monkeypatch):
    """Thirteen selected clusters make two batches. If the second call raises, the first
    batch's verdicts still apply; the failed groups read 'no verdict'."""
    stories = [{"cluster_index": i, "article_ids": [f"A{i}a"]} for i in range(13)]
    (tmp_path / "selected.json").write_text(
        json.dumps({"must_know": stories, "should_know": [], "not_covered_blurb": ""})
    )
    (tmp_path / "clusters.json").write_text(
        json.dumps({"clusters": [{"story": f"s{i}", "article_ids": [f"A{i}a", f"A{i}b"]} for i in range(13)]})
    )
    rows = "\n".join(f"A{i}{s},src,title {i}{s},2026,sum" for i in range(13) for s in "ab")
    (tmp_path / "articles_1.csv").write_text("article_id,source_id,title,published,summary\n" + rows + "\n")
    calls = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        if "GROUP 12" in prompt:
            raise RuntimeError("529")
        results = [{"group": g, "events": [[f"A{g}a"], [f"A{g}b"]]} for g in range(12)]
        return StageResult(
            subtype="success",
            text=json.dumps({"results": results}),
            usage={
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            total_cost_usd=0.01,
            duration_ms=1,
            is_error=False,
        )

    monkeypatch.setattr(cohesion.claude_cli, "run_agent", fake)
    asyncio.run(cohesion.run_cohesion_stage(tmp_path, model="claude-sonnet-4-6", cwd=None))
    doc = json.loads((tmp_path / cohesion.COHESION_ARTIFACT).read_text())
    assert len(calls) == 2
    assert doc["outcome"] == "partial" and doc["split"] == 12
    assert doc["verdicts"][12]["applied"] is False and doc["verdicts"][12]["reason"] == "no verdict"


def test_the_artifact_is_archived_with_the_run():
    """claude_input/ is rebuilt every run; a verdict that only lived there could never be
    replayed or counted. Same guard as cluster_tags.json."""
    import db

    assert cohesion.COHESION_ARTIFACT in db._TRACE_ARTIFACTS


def test_scoring_a_verdict_against_a_label():
    """The replay measurement's per-cluster score: event-count agreement, stray-set Jaccard,
    over-splits (labelled same-event ids the judge put outside the dominant), and whether
    each must-separate id was separated."""
    import eval_cohesion

    ids = ["A1", "A2", "A3", "A4"]
    label = {"n_events": 2, "strays": ["A4"]}
    verdict = {
        "applied": True,
        "events": [["A1", "A2"], ["A3"], ["A4"]],
        "dominant": ["A1", "A2"],
        "strays": ["A3", "A4"],
    }
    s = eval_cohesion.score_verdict(ids, verdict, label, must_separate=["A4"])
    assert s["n_events"] == 3 and s["count_agrees"] is False
    assert s["jaccard"] == 0.5
    assert s["over_splits"] == ["A3"]
    assert s["separated"] == {"A4": True}
    unapplied = eval_cohesion.score_verdict(
        ids, {"applied": False, "events": None, "dominant": ids, "strays": []}, label, must_separate=["A4"]
    )
    assert unapplied["n_events"] == 1 and unapplied["jaccard"] == 0.0 and unapplied["separated"] == {"A4": False}
