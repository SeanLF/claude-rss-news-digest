"""Tests for the within-story why_it_matters judge (eval_why_judge).

The headline test locks the validated metric: the cached v2 judge verdicts in
why_judge_golden.json must agree with the independent human labels at the level
established during validation (agreement 0.867, filler precision 0.867). If the
judge prompt changes, regenerate the cached verdicts and these thresholds catch
a regression below the validated bar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_why_judge import (
    WhyCase,
    _parse_verdict,
    load_golden_cases,
    score_agreement,
)

GOLDEN = Path(__file__).parent / "fixtures" / "why_judge_golden.json"


# --------------------------------------------------------------------------- #
# Validated-metric lock (the cached v2 verdicts vs labels)
# --------------------------------------------------------------------------- #


def test_golden_loads_all_cases():
    cases = load_golden_cases(GOLDEN)
    assert len(cases) == 45
    assert all(isinstance(c, WhyCase) for c in cases)


def test_validated_agreement_meets_bar():
    report = score_agreement(load_golden_cases(GOLDEN))
    # Validated at 0.867; lock just below to catch a real regression without
    # being brittle to a single re-labelled borderline case.
    assert report.agreement_rate >= 0.85, f"agreement regressed: {report.agreement_rate:.3f}"


def test_validated_filler_precision_and_recall():
    report = score_agreement(load_golden_cases(GOLDEN))
    # v2 removed the v1 over-strict bias: precision 0.867, recall 0.765.
    assert report.filler_precision is not None and report.filler_precision >= 0.80
    assert report.filler_recall is not None and report.filler_recall >= 0.70


def test_confusion_cells_sum_to_n():
    report = score_agreement(load_golden_cases(GOLDEN))
    assert report.n == 45
    assert report.tp + report.fp + report.fn + report.tn == 45


# --------------------------------------------------------------------------- #
# Scorer unit tests (filler = positive class)
# --------------------------------------------------------------------------- #


def _case(judge_adds: bool, label_adds: bool) -> WhyCase:
    return WhyCase("h", "s", "w", judge_adds=judge_adds, label_adds=label_adds)


def test_scorer_perfect_agreement():
    cases = [_case(True, True), _case(False, False)]
    report = score_agreement(cases)
    assert report.agreement_rate == 1.0
    assert report.tp == 1 and report.tn == 1 and report.fp == 0 and report.fn == 0
    assert report.disagreements == []


def test_scorer_missed_filler_is_false_negative():
    # judge says adds, label says filler -> a missed defect (fn)
    report = score_agreement([_case(judge_adds=True, label_adds=False)])
    assert report.fn == 1 and report.tp == 0
    assert report.filler_recall == 0.0
    assert len(report.disagreements) == 1


def test_scorer_overflag_is_false_positive():
    # judge says filler, label says adds -> wrongly flagged a good line (fp)
    report = score_agreement([_case(judge_adds=False, label_adds=True)])
    assert report.fp == 1 and report.tp == 0
    assert report.filler_precision == 0.0


def test_scorer_precision_recall_undefined_when_no_filler_calls():
    report = score_agreement([_case(True, True)])
    assert report.filler_precision is None  # judge flagged nothing
    assert report.filler_recall is None  # label has no filler


# --------------------------------------------------------------------------- #
# Verdict parsing
# --------------------------------------------------------------------------- #


def test_parse_verdict_plain_json():
    adds, reason = _parse_verdict('{"adds_dimension": true, "reason": "new 2026 election date"}')
    assert adds is True and "2026" in reason


def test_parse_verdict_with_surrounding_prose():
    adds, reason = _parse_verdict('Here is my verdict:\n{"adds_dimension": false, "reason": "pure restatement"}\nDone.')
    assert adds is False and reason == "pure restatement"


def test_parse_verdict_skips_echoed_schema_line():
    # The prompt ends with a literal schema example containing `true|false`
    # (invalid JSON); a model that echoes it must not break parsing of the
    # real verdict that follows.
    text = (
        'Per the schema {"adds_dimension": true|false, "reason": "<...>"} '
        'my verdict is:\n{"adds_dimension": true, "reason": "new actor: Trump strike threat"}'
    )
    adds, reason = _parse_verdict(text)
    assert adds is True and "Trump" in reason


def test_parse_verdict_skips_object_without_verdict_field():
    # A leading object lacking adds_dimension is skipped in favour of the real one.
    text = '{"note": "thinking..."} then {"adds_dimension": false, "reason": "restatement"}'
    adds, reason = _parse_verdict(text)
    assert adds is False and reason == "restatement"


def test_parse_verdict_raises_on_no_json():
    with pytest.raises(ValueError):
        _parse_verdict("the line is filler")


def test_parse_verdict_raises_on_missing_field():
    with pytest.raises(ValueError):
        _parse_verdict('{"reason": "no verdict field"}')
