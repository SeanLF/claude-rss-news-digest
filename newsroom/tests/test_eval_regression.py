"""Tests for eval_regression.py (offline regression gate comparison logic).

Covers the baseline-vs-current comparison: pass when equal, fail when an L1
check flips PASS->FAIL, when L2 fail-precision drops, or when the leak count
rises. Inline fixtures only -- no IO, no model calls. The metric *computation*
over the real golden set is exercised end-to-end by the bin script and the
committed eval_baseline.json; here we pin the comparison rules.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_regression import (
    L2Stats,
    compare,
    compute_l2_stats,
    golden_headlines_as_selections,
)


def _metrics(*, golden_l1=None, sel_l1=None, l2=None):
    return {
        "l1_golden_headlines": golden_l1 or {"schema_valid": True, "headline_length": True},
        "l1_selections_fixture": sel_l1 or {"schema_valid": True, "headline_length": True},
        "l2_judge_agreement": l2
        or {"labeled_cases": 10, "agreement_rate": 1.0, "fail_precision": 1.0, "leak_count": 0},
    }


# --------------------------------------------------------------------------- #
# Pass when equal
# --------------------------------------------------------------------------- #


def test_identical_metrics_pass():
    base = _metrics()
    result = compare(base, _metrics())
    assert result.passed
    assert result.regressions == []


def test_improvement_passes_but_notes():
    base = _metrics(sel_l1={"schema_valid": False, "headline_length": True})
    cur = _metrics(sel_l1={"schema_valid": True, "headline_length": True})
    result = compare(base, cur)
    assert result.passed
    assert any("improved" in n and "schema_valid" in n for n in result.notes)


# --------------------------------------------------------------------------- #
# L1 regressions
# --------------------------------------------------------------------------- #


def test_l1_check_flipping_pass_to_fail_regresses():
    base = _metrics()
    cur = _metrics(golden_l1={"schema_valid": False, "headline_length": True})
    result = compare(base, cur)
    assert not result.passed
    assert any("schema_valid" in r and "PASS -> FAIL" in r for r in result.regressions)


def test_l1_already_failing_check_staying_failed_is_not_a_regression():
    base = _metrics(sel_l1={"schema_valid": False, "headline_length": True})
    cur = _metrics(sel_l1={"schema_valid": False, "headline_length": True})
    result = compare(base, cur)
    assert result.passed


def test_l1_new_check_is_a_note_not_a_regression():
    base = _metrics()
    cur = _metrics(golden_l1={"schema_valid": True, "headline_length": True, "brand_new": True})
    result = compare(base, cur)
    assert result.passed
    assert any("new check" in n and "brand_new" in n for n in result.notes)


# --------------------------------------------------------------------------- #
# L2 regressions
# --------------------------------------------------------------------------- #


def test_l2_leak_count_rise_regresses():
    base = _metrics()
    cur = _metrics(l2={"labeled_cases": 10, "agreement_rate": 0.9, "fail_precision": 1.0, "leak_count": 2})
    result = compare(base, cur)
    assert not result.passed
    assert any("leak count rose" in r for r in result.regressions)


def test_l2_labeled_cases_drop_regresses():
    # A shrunk/degraded golden set (fewer cases) is a hard regression even when
    # leak_count and fail_precision still look fine -- guards the hollow-gate hole
    # where an empty set (n=0) would otherwise pass clean.
    base = _metrics(l2={"labeled_cases": 386, "agreement_rate": 1.0, "fail_precision": 1.0, "leak_count": 0})
    cur = _metrics(l2={"labeled_cases": 50, "agreement_rate": 1.0, "fail_precision": 1.0, "leak_count": 0})
    result = compare(base, cur)
    assert not result.passed
    assert any("labeled cases dropped" in r for r in result.regressions)


def test_l2_leak_count_drop_passes_with_note():
    base = _metrics(l2={"labeled_cases": 10, "agreement_rate": 0.9, "fail_precision": 1.0, "leak_count": 3})
    cur = _metrics(l2={"labeled_cases": 10, "agreement_rate": 1.0, "fail_precision": 1.0, "leak_count": 0})
    result = compare(base, cur)
    assert result.passed
    assert any("leak count improved" in n for n in result.notes)


def test_l2_fail_precision_drop_regresses():
    base = _metrics()
    cur = _metrics(l2={"labeled_cases": 10, "agreement_rate": 1.0, "fail_precision": 0.8, "leak_count": 0})
    result = compare(base, cur)
    assert not result.passed
    assert any("fail-precision dropped" in r for r in result.regressions)


def test_l2_fail_precision_becoming_undefined_regresses():
    base = _metrics()
    cur = _metrics(l2={"labeled_cases": 10, "agreement_rate": 1.0, "fail_precision": None, "leak_count": 0})
    result = compare(base, cur)
    assert not result.passed
    assert any("fail-precision became undefined" in r for r in result.regressions)


def test_l2_fail_precision_improvement_passes():
    base = _metrics(l2={"labeled_cases": 10, "agreement_rate": 1.0, "fail_precision": 0.8, "leak_count": 0})
    cur = _metrics()
    result = compare(base, cur)
    assert result.passed
    assert any("fail-precision improved" in n for n in result.notes)


# --------------------------------------------------------------------------- #
# Metric computation over the golden set (lightweight, no model calls)
# --------------------------------------------------------------------------- #


def test_compute_l2_stats_keeps_all_cases_despite_duplicate_headline():
    # Two cases share a headline but carry opposite verdicts; the index-suffix
    # join must keep both (n == len(cases)), not collapse them.
    golden = {
        "cases": [
            {"headline": "dup", "judge_pass": True, "label_pass": True},
            {"headline": "dup", "judge_pass": False, "label_pass": False},
            {"headline": "other", "judge_pass": True, "label_pass": True},
        ]
    }
    stats = compute_l2_stats(golden)
    assert isinstance(stats, L2Stats)
    assert stats.labeled_cases == 3
    assert stats.agreement_rate == 1.0
    assert stats.leak_count == 0


def test_compute_l2_stats_counts_a_leak():
    golden = {"cases": [{"headline": "h", "judge_pass": True, "label_pass": False}]}
    stats = compute_l2_stats(golden)
    assert stats.leak_count == 1
    assert stats.agreement_rate == 0.0


def test_golden_headlines_projection_is_selections_shaped():
    golden = {"cases": [{"headline": "first"}, {"headline": "second"}, {"headline": "third"}]}
    sel = golden_headlines_as_selections(golden)
    assert sel["must_know"][0]["headline"] == "first"
    assert [s["headline"] for s in sel["should_know"]] == ["second", "third"]
    assert isinstance(sel["preheader"], str)
