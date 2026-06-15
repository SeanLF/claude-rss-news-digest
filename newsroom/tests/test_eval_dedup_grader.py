"""Tests for the cross-story semantic dedup grader (eval_dedup_grader).

The headline test locks the validated metric: at threshold 0.57 the redundancy
screen (same-event OR partial overlap vs distinct) scores precision 1.0 /
recall 0.95 on the 60-pair golden. Pure code off the committed cosines -- no
embedding model, so it runs in CI without torch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_dedup_grader import (
    DEDUP_THRESHOLD,
    REDUNDANT_LABELS,
    DedupCase,
    cosine_sim,
    load_golden_cases,
    score_threshold,
    story_text,
)

GOLDEN = Path(__file__).parent / "fixtures" / "dedup_golden.json"


# --------------------------------------------------------------------------- #
# Validated-metric lock
# --------------------------------------------------------------------------- #


def test_golden_loads_all_pairs():
    cases = load_golden_cases(GOLDEN)
    assert len(cases) == 60
    assert all(isinstance(c, DedupCase) for c in cases)


def test_redundant_derivation_matches_labels():
    # redundant == (label is y or partial); distinct == n
    for c in load_golden_cases(GOLDEN):
        assert c.redundant == (c.label_same_event in REDUNDANT_LABELS)


def test_validated_threshold_precision_recall():
    report = score_threshold(load_golden_cases(GOLDEN), DEDUP_THRESHOLD)
    assert report.n == 60
    # Validated: precision 1.0 (zero distinct pairs flagged), recall 0.95.
    assert report.precision == 1.0, f"precision regressed: {report.precision}"
    assert report.recall is not None and report.recall >= 0.90, f"recall regressed: {report.recall}"


def test_distinct_pairs_never_flagged_at_threshold():
    # The clean property: every label='n' (distinct) pair is below threshold.
    report = score_threshold(load_golden_cases(GOLDEN), DEDUP_THRESHOLD)
    assert report.fp == 0


def test_chosen_threshold_beats_the_plans_070():
    # The plan's 0.70 was mis-targeted; 0.57 strictly dominates it on this golden.
    cases = load_golden_cases(GOLDEN)
    good = score_threshold(cases, 0.57)
    plan = score_threshold(cases, 0.70)
    assert good.recall > plan.recall  # 0.70 misses real redundancies that 0.57 catches


# --------------------------------------------------------------------------- #
# Similarity primitives
# --------------------------------------------------------------------------- #


def test_cosine_identical_is_one():
    assert cosine_sim([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_negative_one():
    assert cosine_sim([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_is_zero():
    assert cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_raises():
    with pytest.raises(ValueError):
        cosine_sim([1.0], [1.0, 2.0])


def test_story_text_joins_headline_and_summary():
    assert story_text({"headline": "Quake hits", "summary": "41 dead"}) == "Quake hits. 41 dead"


def test_story_text_tolerates_missing_fields():
    assert story_text({"headline": "Solo"}) == "Solo."
    assert story_text({}) == "."


# --------------------------------------------------------------------------- #
# Scorer logic
# --------------------------------------------------------------------------- #


def _case(cosine: float, redundant: bool, label: str = "y") -> DedupCase:
    return DedupCase(cosine=cosine, label_same_event=label, redundant=redundant)


def test_scorer_confusion_cells():
    cases = [
        _case(0.80, True),  # tp
        _case(0.50, True),  # fn
        _case(0.80, False, "n"),  # fp
        _case(0.50, False, "n"),  # tn
    ]
    report = score_threshold(cases, 0.57)
    assert (report.tp, report.fn, report.fp, report.tn) == (1, 1, 1, 1)
    assert report.precision == 0.5 and report.recall == 0.5
    assert report.accuracy == 0.5


def test_scorer_precision_none_when_nothing_flagged():
    report = score_threshold([_case(0.10, False, "n")], 0.57)
    assert report.precision is None
