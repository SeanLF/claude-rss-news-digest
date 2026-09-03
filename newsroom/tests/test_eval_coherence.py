"""The coherence eval's per-story single-turn arm: one call per story, merged in draft order."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import eval_coherence

_BODY = (
    "**Instructions:**\n1. read things\n"
    "3. Use the Write tool to write the result to `/app/data/claude_input/coherence_report.json`\n"
    "**For each field, run all three probes** x\n"
    "**Output schema** y\n"
    "- DO NOT use Bash. Use Read and Write tools only.\n"
    "- Check EVERY story (must_know and should_know). z\n"
)


def test_per_story_reports_merge_in_draft_order(tmp_path, monkeypatch):
    """17 calls produce 17 results, one per story, in the draft's order, so score() maps
    headlines exactly as it does for the multi-turn report."""
    (tmp_path / "draft_selections.json").write_text(
        json.dumps(
            {
                "must_know": [
                    {"headline": "One", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}
                ],
                "should_know": [{"headline": "Two", "summary": "s", "sources": [{"article_id": "A2"}]}],
                "preheader": "p",
            }
        )
    )
    (tmp_path / "articles_1.csv").write_text("article_id,title,summary\nA1,a,b\nA2,c,d\n")
    seen: list[str] = []

    async def fake_run_agent(prompt, **kw):
        head = json.loads(prompt.split("## draft_selections.json\n\n", 1)[1].split("\n\n## ", 1)[0])
        h = head["must_know"][0]["headline"]
        seen.append(h)
        assert kw["tools"] == [] and kw["max_turns"] == 1

        return SimpleNamespace(
            ok=True,
            text=json.dumps(
                {
                    "results": [
                        {
                            "headline": h,
                            "article_ids": ["A1"],
                            "pass": h == "One",
                            "reason": "" if h == "One" else "summary: x",
                            "failed_fields": [] if h == "One" else ["summary"],
                        }
                    ]
                }
            ),
            usage={},
            total_cost_usd=0.0,
        )

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake_run_agent)
    out = tmp_path / "coherence_report.json"
    asyncio.run(eval_coherence.run_per_story_to_file(out, "m", _BODY, {"type": "disabled"}, tmp_path))
    report = json.loads(out.read_text())
    assert [r["headline"] for r in report["results"]] == ["One", "Two"]
    assert [r["pass"] for r in report["results"]] == [True, False]
    assert sorted(seen) == ["One", "Two"]


def test_every_run_keeps_its_own_report(tmp_path, monkeypatch):
    """Five per-story runs overwrote one coherence_report.json; the three idx-16 false-drop
    reasons from runs 1-3 of the 2026-09-03 measurement were lost to it. Each run's report
    is now kept as coherence_report.<n>.json next to the live one."""
    (tmp_path / "labels.json").write_text(
        json.dumps(
            {
                "hard_positives": [{"idx": 0, "field": "summary"}],
                "borderline": [],
                "clean_fields": [{"idx": 0, "field": "headline"}],
                "idx_headlines": {"0": "One"},
            }
        )
    )
    calls = {"n": 0}

    async def fake_run(label, out_path, model, body, thinking, tools):
        calls["n"] += 1
        out_path.write_text(
            json.dumps(
                {
                    "results": [
                        {"headline": "One", "pass": False, "reason": f"run {calls['n']}", "failed_fields": ["summary"]}
                    ]
                }
            )
        )

    monkeypatch.setattr(eval_coherence, "run_agent_to_file", fake_run)
    monkeypatch.setattr(
        eval_coherence,
        "load_agent_for_eval",
        lambda agent, fixtures, override=None: ("m", "body", {"type": "disabled"}, ["Read", "Write"]),
    )
    monkeypatch.setattr(sys, "argv", ["eval_coherence", "--runs", "3", "--fixtures", str(tmp_path)])
    assert eval_coherence.main() == 0
    kept = sorted(p.name for p in tmp_path.glob("coherence_report.*.json"))
    assert kept == ["coherence_report.0.json", "coherence_report.1.json", "coherence_report.2.json"]
    assert json.loads((tmp_path / "coherence_report.1.json").read_text())["results"][0]["reason"] == "run 2"
