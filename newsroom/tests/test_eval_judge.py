"""Tests for eval_judge.py (L2 trust-chain harness).

Covers the join logic (coherence verdict -> resolved source articles, with
article_id fallback from draft_selections) and score_agreement's 2x2 confusion
matrix. Small inline fixtures, no IO. The key test builds a sample where judge
and label disagree in both directions and asserts each matrix cell.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_judge import (
    JudgeCase,
    LabeledCase,
    build_cases,
    score_agreement,
)

# --------------------------------------------------------------------------- #
# join / build_cases
# --------------------------------------------------------------------------- #


def _article_index():
    return {
        "A1": {"original_title": "Cats invade city hall", "name": "Cat News", "source_id": "cat"},
        "A2": {"original_title": "Markets dip on rate fears", "name": "Fin Daily", "source_id": "fin"},
    }


def test_build_cases_resolves_article_ids_from_report():
    report = {
        "results": [
            {"headline": "Cats take over", "article_ids": ["A1"], "pass": True, "reason": "ok"},
        ]
    }
    cases = build_cases(report, _article_index())
    assert len(cases) == 1
    case = cases[0]
    assert case.headline == "Cats take over"
    assert case.judge_pass is True
    assert case.judge_reason == "ok"
    assert len(case.articles) == 1
    assert case.articles[0].id == "A1"
    assert case.articles[0].title == "Cats invade city hall"
    assert case.articles[0].source == "Cat News"


def test_build_cases_falls_back_to_draft_selections_for_article_ids():
    # Coherence result omits article_ids; they must be recovered from the draft.
    report = {"results": [{"headline": "Markets wobble", "pass": False, "reason": "drift"}]}
    draft = {
        "should_know": [
            {"headline": "Markets wobble", "sources": [{"article_id": "A2"}]},
        ],
        "must_know": [],
    }
    cases = build_cases(report, _article_index(), draft)
    assert len(cases) == 1
    assert cases[0].judge_pass is False
    assert [a.id for a in cases[0].articles] == ["A2"]
    assert cases[0].articles[0].title == "Markets dip on rate fears"


def test_build_cases_unknown_article_id_surfaces_empty_ref():
    report = {"results": [{"headline": "Mystery", "article_ids": ["A999"], "pass": True}]}
    cases = build_cases(report, _article_index())
    assert len(cases[0].articles) == 1
    assert cases[0].articles[0].id == "A999"
    assert cases[0].articles[0].title == ""


# --------------------------------------------------------------------------- #
# score_agreement / confusion matrix
# --------------------------------------------------------------------------- #


def _case(headline, judge_pass):
    return JudgeCase(headline=headline, articles=[], judge_pass=judge_pass, judge_reason="r")


def _label(headline, label_pass):
    return LabeledCase(headline=headline, label_pass=label_pass, label_rationale="lr")


def test_confusion_matrix_all_four_cells():
    # One case in each quadrant of the judge x label matrix.
    cases = [
        _case("agree-pass", judge_pass=True),  # pass & faithful
        _case("false-pass", judge_pass=True),  # pass & unfaithful  -> leak
        _case("false-fail", judge_pass=False),  # fail & faithful   -> wrongly dropped
        _case("agree-fail", judge_pass=False),  # fail & unfaithful
    ]
    labels = [
        _label("agree-pass", label_pass=True),
        _label("false-pass", label_pass=False),
        _label("false-fail", label_pass=True),
        _label("agree-fail", label_pass=False),
    ]
    report = score_agreement(cases, labels)

    assert report.pass_faithful == 1
    assert report.pass_unfaithful == 1
    assert report.fail_faithful == 1
    assert report.fail_unfaithful == 1
    assert report.n == 4
    assert report.agreements == 2
    assert report.agreement_rate == 0.5

    # Two disagreements: the false-pass and the false-fail.
    headlines = {d.headline for d in report.disagreements}
    assert headlines == {"false-pass", "false-fail"}

    assert report.pass_precision == 0.5  # 1 of 2 passes faithful
    assert report.fail_precision == 0.5  # 1 of 2 fails truly unfaithful


def test_all_pass_sample_leaves_fail_precision_undefined():
    # Mirrors the real April sample: judge passes everything, all labels agree.
    cases = [_case("h1", True), _case("h2", True)]
    labels = [_label("h1", True), _label("h2", True)]
    report = score_agreement(cases, labels)

    assert report.pass_faithful == 2
    assert report.pass_unfaithful == 0
    assert report.fail_faithful == 0
    assert report.fail_unfaithful == 0
    assert report.agreement_rate == 1.0
    assert report.pass_precision == 1.0
    assert report.fail_precision is None  # FAIL behavior unvalidated
    assert report.disagreements == []


def test_unlabeled_judge_cases_excluded_from_matrix():
    cases = [_case("labeled", True), _case("orphan", True)]
    labels = [_label("labeled", True)]
    report = score_agreement(cases, labels)

    assert report.n == 1
    assert report.pass_faithful == 1
    assert report.unlabeled == ["orphan"]


def test_empty_inputs_agreement_rate_defaults_to_one():
    report = score_agreement([], [])
    assert report.n == 0
    assert report.agreement_rate == 1.0
    assert report.pass_precision is None
    assert report.fail_precision is None
