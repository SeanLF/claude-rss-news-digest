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
