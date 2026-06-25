"""Tests for eval_recap_judge: the L2 RECAP theme-coverage judge.

Mirrors eval_why_judge: a live judge (model call, not unit-tested) plus pure-code
offline scoring of judge verdicts against an independent human-labelled golden.
The defect this judge detects is OMISSION (a prominent theme in the titles the
recap drops) or FABRICATION (a theme the recap asserts that the titles don't
support). The positive class for scoring is "defective" (not clean).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_recap_judge import (
    RecapCase,
    load_theme_golden,
    parse_recap_verdict,
    prepare_titles,
    score_agreement,
    theme_precision,
)

GOLDEN = Path(__file__).parent / "fixtures" / "recap_judge_golden.json"


def test_parse_recap_verdict_extracts_theme_lists():
    text = 'Here is my verdict: {"missed_themes": ["EU-China trade"], "fabricated_themes": []} done.'
    missed, fabricated = parse_recap_verdict(text)
    assert missed == ["EU-China trade"]
    assert fabricated == []


def test_parse_recap_verdict_skips_a_schema_echo_and_finds_the_real_object():
    # The prompt ends with a literal schema example; a model that echoes it must
    # not derail parsing (same hazard the why-judge guards against).
    text = (
        'schema: {"missed_themes": ["<theme>"], "fabricated_themes": ["<theme>"]}\n'
        'answer: {"missed_themes": [], "fabricated_themes": ["a Mars landing"]}'
    )
    missed, fabricated = parse_recap_verdict(text)
    assert missed == []
    assert fabricated == ["a Mars landing"]


def test_parse_recap_verdict_raises_when_no_object_present():
    with pytest.raises(ValueError):
        parse_recap_verdict("I could not find any themes.")


def test_prepare_titles_dedupes_exact_normalized_duplicates():
    titles = [
        "Senate votes to halt Iran war in rare rebuke to Trump",
        "U.S. Senate votes to halt Iran war, bucking Trump",
        "Senate votes to halt Iran war in rare rebuke to Trump",  # exact dup of #1
        "EU and China escalate trade tensions",
    ]
    out = prepare_titles(titles, cap=10)
    assert out.count("Senate votes to halt Iran war in rare rebuke to Trump") == 1
    assert len(out) == 3


def test_prepare_titles_caps_count():
    titles = [f"distinct story number {i} on a separate topic" for i in range(50)]
    out = prepare_titles(titles, cap=10)
    assert len(out) == 10


def test_score_agreement_treats_a_missed_defect_as_a_false_negative():
    cases = [
        RecapCase(window_id="w1", model="haiku", judge_clean=True, label_clean=False),  # missed -> fn
        RecapCase(window_id="w2", model="haiku", judge_clean=False, label_clean=False),  # caught -> tp
        RecapCase(window_id="w3", model="sonnet", judge_clean=False, label_clean=True),  # over-flag -> fp
        RecapCase(window_id="w4", model="sonnet", judge_clean=True, label_clean=True),  # agree clean -> tn
    ]
    rep = score_agreement(cases)
    assert (rep.tp, rep.fp, rep.fn, rep.tn) == (1, 1, 1, 1)
    assert rep.agreement_rate == 0.5
    assert rep.defect_recall == 0.5  # caught 1 of 2 real defects


def test_theme_precision_pure_function_counts_real_over_flagged():
    cases = [
        {"label_real": True},
        {"label_real": True},
        {"label_real": False},
        {"label_real": True},
    ]
    assert theme_precision(cases) == 0.75


def test_recap_judge_theme_precision_stays_above_floor():
    # Regression guard: if a judge-prompt change drops precision (it starts
    # flagging non-prominent / filtered topics as missed major themes), fail.
    cases = load_theme_golden(GOLDEN)
    p = theme_precision(cases)
    assert p >= 0.9, f"recap-judge theme precision {p:.3f} below 0.90 floor ({len(cases)} flagged themes)"
