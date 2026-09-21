"""The coherence eval's per-story single-turn arm: one call per story, merged in draft order."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import eval_coherence

_BODY = (
    "**Instructions:**\n1. Use the Read tool to read these files:\n   - x\n"
    "2. For each story in draft_selections.json check it. A specific that appears solely in a non-cited article "
    "counts as UNSUPPORTED.\n"
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
        lambda agent, fixtures, override=None, **_: ("m", "body", {"type": "disabled"}, ["Read", "Write"]),
    )
    monkeypatch.setattr(sys, "argv", ["eval_coherence", "--runs", "3", "--fixtures", str(tmp_path)])
    assert eval_coherence.main() == 0
    kept = sorted(p.name for p in tmp_path.glob("coherence_report.*.json"))
    assert kept == ["coherence_report.0.json", "coherence_report.1.json", "coherence_report.2.json"]
    assert json.loads((tmp_path / "coherence_report.1.json").read_text())["results"][0]["reason"] == "run 2"


def test_score_reports_failure_kinds_against_the_label_type(tmp_path):
    """A report carrying `failure_kinds` (the VeriGray-style contradicted/unsupported split) is
    tallied per flagged field and compared with what the label's error type implies; a report
    without it scores exactly as before (the field is optional instrumentation)."""
    labels = {
        "idx_headlines": {"0": "Alpha", "1": "Beta", "2": "Gamma"},
        "hard_positives": [
            {"idx": 0, "field": "summary", "type": "EntE-wrong-entity"},
            {"idx": 1, "field": "summary", "type": "OutE-absence"},
            {"idx": 2, "field": "headline", "type": "LinkE-fabricated-causal"},
        ],
        "borderline": [{"idx": 1, "field": "headline", "type": "invented-precision"}],
        "clean_fields": [{"idx": 0, "field": "headline"}],
    }
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "headline": "Alpha",
                        "pass": False,
                        "failed_fields": ["summary"],
                        "failure_kinds": {"summary": "contradicted"},
                    },
                    {
                        "headline": "Beta",
                        "pass": False,
                        "failed_fields": ["summary"],
                        "failure_kinds": {"summary": "contradicted"},
                    },
                    {
                        "headline": "Gamma",
                        "pass": False,
                        "failed_fields": ["headline"],
                        "failure_kinds": {"headline": "nonsense"},
                    },
                ]
            }
        )
    )
    s = eval_coherence.score(report, labels)
    assert s["kinds"] == {"0:summary": "contradicted", "1:summary": "contradicted"}  # the bad value is not counted
    assert s["kind_labelled"] == 2
    assert s["kind_agree"] == [(0, "summary")]  # Beta was OutE (expected unsupported), Gamma malformed
    assert s["kind_disagree"] == [(1, "summary", "unsupported", "contradicted")]
    assert s["malformed"] == ["unknown failure_kinds value 'nonsense' (headline='Gamma')"]
    # LinkE is scored neither way, so a valid kind on Gamma would be counted but not judged.
    report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "headline": "Gamma",
                        "pass": False,
                        "failed_fields": ["headline"],
                        "failure_kinds": {"headline": "contradicted"},
                    }
                ]
            }
        )
    )
    s = eval_coherence.score(report, labels)
    assert s["kind_labelled"] == 1 and s["kind_agree"] == [] and s["kind_disagree"] == []
    assert s["kind_unscored"] == [(2, "headline", "LinkE-fabricated-causal")]  # visible, not silent
    assert s["kind_other"] == 0
    # A kind on a field the entry did NOT flag is noise, never an agreement on a missed positive.
    report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "headline": "Beta",
                        "pass": False,
                        "failed_fields": ["headline"],
                        "failure_kinds": {"headline": "contradicted", "summary": "unsupported"},
                    }
                ]
            }
        )
    )
    s = eval_coherence.score(report, labels)
    assert s["hard_missed"] == [(0, "summary"), (1, "summary"), (2, "headline")]
    # Beta:headline is a BORDERLINE label (invented-precision -> unsupported): judged, and wrong here.
    assert s["kinds"] == {"1:headline": "contradicted"} and s["kind_agree"] == []
    assert s["kind_disagree"] == [(1, "headline", "unsupported", "contradicted")]
    assert s["kind_other"] == 0
    assert s["malformed"] == ["failure_kinds names unflagged field 'summary' (headline='Beta')"]

    report.write_text(json.dumps({"results": [{"headline": "Alpha", "pass": False, "failed_fields": ["summary"]}]}))
    s = eval_coherence.score(report, labels)
    assert s["kinds"] == {} and s["kind_labelled"] == 0 and s["kind_agree"] == [] and s["malformed"] == []


def test_expected_kind_follows_the_label_type_prefix_and_never_defaults():
    assert eval_coherence.expected_kind("OutE-absence") == "unsupported"
    assert eval_coherence.expected_kind("LinkE-fabricated-causal") is None
    assert eval_coherence.expected_kind("unsupported-specific") == "unsupported"
    assert eval_coherence.expected_kind("EntE-wrong-entity") == "contradicted"
    assert eval_coherence.expected_kind("quantifier-overstatement") == "contradicted"
    assert eval_coherence.expected_kind("CircE-scope + EntE") == "contradicted"
    # An unknown, typo'd, empty or missing type must not be scored as either side.
    for t in ("", "out-dependent", "OuteE-typo", "overstatement", None, 3):
        assert eval_coherence.expected_kind(t) is None, t


def test_score_refuses_a_field_labelled_both_hard_and_borderline(tmp_path):
    labels = {
        "idx_headlines": {"0": "Alpha"},
        "hard_positives": [{"idx": 0, "field": "summary", "type": "EntE-wrong-entity"}],
        "borderline": [{"idx": 0, "field": "summary", "type": "OutE-absence"}],
        "clean_fields": [],
    }
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"results": [{"headline": "Alpha", "pass": False, "failed_fields": ["summary"]}]}))
    with pytest.raises(RuntimeError, match="both hard positive and borderline"):
        eval_coherence.score(report, labels)


def _hangs_then_writes(out_path, fail_times):
    calls = {"n": 0}

    async def fake_run_agent(prompt, **kw):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError("SDK idle timeout: no event in 180.0s")
        out_path.write_text(json.dumps({"results": []}))
        return SimpleNamespace(ok=True, error_summary=lambda: "", total_cost_usd=0.0, usage={})

    return fake_run_agent, calls


def test_a_rep_that_hangs_once_is_retried_not_lost(tmp_path, monkeypatch):
    # Two five-rep runs on 2026-09-18 each died on one idle timeout, losing every rep after it.
    out = tmp_path / "coherence_report.json"
    fake, calls = _hangs_then_writes(out, fail_times=1)
    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)

    asyncio.run(eval_coherence.run_agent_to_file("coherence", out, "m", "body", {"type": "disabled"}, ["Read"]))

    assert calls["n"] == 2
    assert out.exists()


def test_a_rep_that_hangs_twice_fails_loud(tmp_path, monkeypatch):
    out = tmp_path / "coherence_report.json"
    fake, calls = _hangs_then_writes(out, fail_times=2)
    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)

    with pytest.raises(RuntimeError, match="idle timeout"):
        asyncio.run(eval_coherence.run_agent_to_file("coherence", out, "m", "body", {"type": "disabled"}, ["Read"]))
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# I/O-shape arms (2026-09-21): read-text, read-schema, inline-grep
# ---------------------------------------------------------------------------

_REPORT = {"results": [{"headline": "One", "article_ids": ["A1"], "pass": True, "reason": "ok"}]}


def _fixtures(tmp_path):
    (tmp_path / "draft_selections.json").write_text(json.dumps({"must_know": [{"headline": "One"}], "should_know": []}))
    (tmp_path / "articles_1.csv").write_text("article_id,title,summary\nA1,a,b\n")
    return tmp_path


def _res(*, text="", structured_output=None, tool_calls=(), ok=True):
    return SimpleNamespace(
        ok=ok,
        text=text,
        structured_output=structured_output,
        tool_calls=tool_calls,
        usage={"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4},
        total_cost_usd=0.5,
        duration_ms=1000,
        error_summary=lambda: "err",
    )


def test_read_schema_arm_prefers_structured_output_and_sends_the_schema(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path)
    seen = {}

    async def fake(prompt, **kw):
        seen.update(kw)
        return _res(text="not json", structured_output=_REPORT, tool_calls=(("Read", "/x"),))

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)
    out = fx / "coherence_report.json"
    m = asyncio.run(eval_coherence.run_arm_to_file("read-schema", out, "m", _BODY, {"type": "adaptive"}, fx))

    assert json.loads(out.read_text()) == _REPORT
    assert seen["output_format"]["type"] == "json_schema"
    assert seen["tools"] == ["Read"]
    assert m["attempts"] == 1 and m["reads"] == 1 and m["greps"] == 0 and m["writes"] == 0


def test_read_text_arm_parses_the_final_message_and_retries_once_when_unparseable(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path)
    calls = {"n": 0}

    async def fake(prompt, **kw):
        calls["n"] += 1
        assert kw.get("output_format") is None and kw["tools"] == ["Read"]
        return _res(text="garbage" if calls["n"] == 1 else json.dumps(_REPORT))

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)
    out = fx / "coherence_report.json"
    m = asyncio.run(eval_coherence.run_arm_to_file("read-text", out, "m", _BODY, {"type": "adaptive"}, fx))

    assert json.loads(out.read_text()) == _REPORT
    assert m["attempts"] == 2
    assert m["cost"] == 1.0  # both attempts are paid for


def test_inline_grep_arm_inlines_the_corpus_allows_grep_and_counts_unbacked_fails(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path)
    seen = {}
    failing = {
        "results": [
            {
                "headline": "One",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "summary: x",
                "failed_fields": ["summary"],
            }
        ]
    }

    async def fake(prompt, **kw):
        seen.update(kw)
        seen["prompt"] = prompt
        return _res(text=json.dumps(failing), tool_calls=())  # nothing behind the FAIL

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)
    out = fx / "coherence_report.json"
    m = asyncio.run(eval_coherence.run_arm_to_file("inline-grep", out, "m", _BODY, {"type": "adaptive"}, fx))

    assert "## articles_1.csv" in seen["prompt"]
    assert sorted(seen["tools"]) == ["Grep", "Read"]
    assert m["failed_fields"] == 1 and m["greps"] == 0 and m["unbacked_fails"] == 1


def test_unknown_arm_is_refused(tmp_path):
    with pytest.raises(ValueError, match="arm"):
        asyncio.run(eval_coherence.run_arm_to_file("bogus", tmp_path / "r.json", "m", _BODY, {}, tmp_path))


def test_tool_counts_and_the_grep_rule_describe_the_accepted_attempt_only(tmp_path, monkeypatch):
    """Review finding 2026-09-21: summing greps across a discarded attempt laundered a FAIL the
    accepted report made with nothing behind it."""
    fx = _fixtures(tmp_path)
    failing = {
        "results": [
            {
                "headline": "One",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "summary: says 58%",
                "failed_fields": ["summary"],
            }
        ]
    }
    calls = {"n": 0}

    async def fake(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _res(text="garbage", tool_calls=(("Grep", "58%"), ("Read", "/a")))
        return _res(text=json.dumps(failing), tool_calls=())

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake)
    m = asyncio.run(eval_coherence.run_arm_to_file("inline-grep", fx / "r.json", "m", _BODY, {}, fx))
    assert m["attempts"] == 2 and m["cost"] == 1.0
    assert m["greps"] == 0 and m["reads"] == 0
    assert m["unbacked_fails"] == 1


def test_a_grep_whose_pattern_names_the_specific_backs_the_fail():
    report = {
        "results": [
            {"headline": "A", "pass": False, "reason": "summary: says 12,000 killed but sources say 8,000"},
            {"headline": "B", "pass": False, "reason": "headline: says Bhutan but sources say Tibet"},
            {"headline": "C", "pass": True, "reason": "ok"},
        ]
    }
    calls = (("Grep", "12,000"), ("Read", "/x"))
    assert eval_coherence.unbacked_fail_count(report, calls) == 1  # B has no grep behind it
    # A generic or tiny pattern backs nothing: the re-review found "." and "kill" backed a whole report.
    assert eval_coherence.unbacked_fail_count(report, (("Grep", "."),)) == 2
    assert eval_coherence.unbacked_fail_count(report, (("Grep", "say"),)) == 2


def test_a_brief_dropped_without_failed_fields_counts_two_fields(tmp_path):
    (tmp_path / "draft_selections.json").write_text(
        json.dumps({"must_know": [{"headline": "Lead"}], "should_know": [{"headline": "Brief"}]})
    )
    report = {
        "results": [
            {"headline": "Lead", "pass": False, "reason": "r"},
            {"headline": "Brief", "pass": False, "reason": "r"},
            {"headline": "Lead", "pass": False, "reason": "r", "failed_fields": ["summary"]},
        ]
    }
    assert eval_coherence.failed_field_count(report, tmp_path) == 3 + 2 + 1


def test_single_turn_cannot_be_combined_with_an_arm(tmp_path, monkeypatch):
    (tmp_path / "labels.json").write_text(
        json.dumps({"hard_positives": [], "borderline": [], "clean_fields": [], "idx_headlines": {}})
    )
    monkeypatch.setattr(
        eval_coherence,
        "load_agent_for_eval",
        lambda agent, fixtures, override=None, **_: ("m", "body", {"type": "disabled"}, ["Read"]),
    )
    monkeypatch.setattr(
        sys, "argv", ["eval_coherence", "--single-turn", "--arm", "inline-grep", "--fixtures", str(tmp_path)]
    )
    with pytest.raises(SystemExit):
        eval_coherence.main()
