"""Tests for eval_regression.py (offline regression gate comparison logic).

Covers the baseline-vs-current comparison: pass when equal, fail when an L1
check flips PASS->FAIL, when L2 agreement or fail-precision drops, when the leak
count rises, or when the blind-labelled subset shrinks. Inline fixtures for the
comparison rules; the committed golden set for the metric computation, which
must score against ``label_blind`` (independent of the judge) and never against
``label_pass`` (a copy of the judge's own verdict).
"""

import copy
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_regression import (
    FixtureError,
    L2Stats,
    compare,
    compare_for_gate,
    compute_l2_stats,
    compute_metrics,
    compute_metrics_for_gate,
    compute_metrics_from_paths,
    golden_headlines_as_selections,
    load_json,
    load_json_for_gate,
)

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIXTURES / "coherence_golden.json"
BASELINE_PATH = FIXTURES / "eval_baseline.json"
SELECTIONS_PATH = FIXTURES / "prod_baseline_selections.json"


def _l2(*, total=20, blind=10, agreement=1.0, fail_precision=1.0, leak_count=0, unresolved=4, unresolved_passed=0):
    return {
        "total_cases": total,
        "blind_labeled_cases": blind,
        "agreement_rate": agreement,
        "fail_precision": fail_precision,
        "leak_count": leak_count,
        "unresolved_sources": unresolved,
        "unresolved_sources_passed": unresolved_passed,
    }


def _metrics(*, golden_l1=None, sel_l1=None, l2=None, stories=386):
    return {
        "l1_golden_stories": stories,
        "l1_golden_headlines": golden_l1 or {"schema_valid": True, "headline_length": True},
        "l1_selections_fixture": sel_l1 or {"schema_valid": True, "headline_length": True},
        "l2_judge_agreement": l2 or _l2(),
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


def test_l1_grading_fewer_stories_regresses():
    # Blank every headline and the projection is empty, so ten of the eleven L1
    # checks report PASS over zero stories while total_cases still reads 386.
    # Vacuous truth in the L1 arm, structurally identical to the n=0 hole the L2
    # population guards close.
    result = compare(_metrics(stories=386), _metrics(stories=0))
    assert not result.passed
    assert any("graded stories dropped" in r for r in result.regressions)


def test_blanking_the_golden_headlines_empties_the_l1_projection():
    golden = copy.deepcopy(load_json(GOLDEN_PATH))
    for case in golden["cases"]:
        case["headline"] = ""
    sel = golden_headlines_as_selections(golden)
    assert sel["must_know"] == [] and sel["should_know"] == []
    assert compute_metrics(golden, load_json(SELECTIONS_PATH))["l1_golden_stories"] == 0


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
    cur = _metrics(l2=_l2(agreement=0.9, leak_count=2))
    result = compare(base, cur)
    assert not result.passed
    assert any("leak count rose" in r for r in result.regressions)


def test_l2_total_cases_drop_regresses():
    # A shrunk/degraded golden set (fewer cases) is a hard regression even when
    # leak_count and fail_precision still look fine -- guards the hollow-gate hole
    # where an empty set (n=0) would otherwise pass clean.
    base = _metrics(l2=_l2(total=386, blind=60))
    cur = _metrics(l2=_l2(total=50, blind=60))
    result = compare(base, cur)
    assert not result.passed
    assert any("golden cases dropped" in r for r in result.regressions)


def test_l2_blind_subset_shrinking_regresses():
    # The blind subset IS the evidence. Dropping blind labels while leaving the
    # golden set intact would silently shrink the only independent signal, and
    # at n=0 agreement_rate reads 1.0 -- the hollow gate again, one level in.
    base = _metrics(l2=_l2(total=386, blind=60))
    cur = _metrics(l2=_l2(total=386, blind=0))
    result = compare(base, cur)
    assert not result.passed
    assert any("blind-labelled cases dropped" in r for r in result.regressions)


def test_l2_agreement_rate_drop_regresses():
    base = _metrics(l2=_l2(agreement=0.9667))
    cur = _metrics(l2=_l2(agreement=0.9))
    result = compare(base, cur)
    assert not result.passed
    assert any("agreement rate dropped" in r for r in result.regressions)


def test_l2_stats_from_dict_rejects_a_stale_baseline_shape():
    stale = {"labeled_cases": 386, "agreement_rate": 1.0, "fail_precision": 1.0, "leak_count": 0}
    with pytest.raises(ValueError, match="--update"):
        L2Stats.from_dict(stale)


def test_l2_stats_from_dict_requires_the_nullable_fail_precision_key():
    # fail_precision may be null, but a MISSING key would read as None via .get()
    # and quietly switch the fail-precision comparison off.
    d = _l2()
    del d["fail_precision"]
    with pytest.raises(ValueError, match="fail_precision"):
        L2Stats.from_dict(d)


def test_l2_leak_count_drop_passes_with_note():
    base = _metrics(l2=_l2(agreement=0.9, leak_count=3))
    cur = _metrics(l2=_l2())
    result = compare(base, cur)
    assert result.passed
    assert any("leak count improved" in n for n in result.notes)


def test_l2_fail_precision_drop_regresses():
    base = _metrics()
    cur = _metrics(l2=_l2(fail_precision=0.8))
    result = compare(base, cur)
    assert not result.passed
    assert any("fail-precision dropped" in r for r in result.regressions)


def test_l2_fail_precision_becoming_undefined_regresses():
    base = _metrics()
    cur = _metrics(l2=_l2(fail_precision=None))
    result = compare(base, cur)
    assert not result.passed
    assert any("fail-precision became undefined" in r for r in result.regressions)


def test_l2_fail_precision_improvement_passes():
    base = _metrics(l2=_l2(fail_precision=0.8))
    cur = _metrics()
    result = compare(base, cur)
    assert result.passed
    assert any("fail-precision improved" in n for n in result.notes)


# --------------------------------------------------------------------------- #
# Metric computation over the golden set (lightweight, no model calls)
# --------------------------------------------------------------------------- #


def test_compute_l2_stats_keeps_all_cases_despite_duplicate_headline():
    # Two cases share a headline but carry opposite verdicts; the index-suffix
    # join must keep both, not collapse them.
    golden = {
        "cases": [
            {"headline": "dup", "judge_pass": True, "label_blind": True},
            {"headline": "dup", "judge_pass": False, "label_blind": False},
            {"headline": "other", "judge_pass": True, "label_blind": True},
        ]
    }
    stats = compute_l2_stats(golden)
    assert isinstance(stats, L2Stats)
    assert stats.total_cases == 3
    assert stats.blind_labeled_cases == 3
    assert stats.agreement_rate == 1.0
    assert stats.leak_count == 0


def test_compute_l2_stats_counts_a_leak():
    golden = {"cases": [{"headline": "h", "judge_pass": True, "label_blind": False}]}
    stats = compute_l2_stats(golden)
    assert stats.leak_count == 1
    assert stats.agreement_rate == 0.0


def test_a_non_boolean_label_blind_raises_instead_of_being_coerced():
    # "I looked and could not decide" is the natural thing to write into this
    # field. bool() would turn null/"" into UNFAITHFUL and "unsure" into
    # FAITHFUL -- a fabricated verdict that moves leak_count. Fail loudly.
    for value in (None, "", "unsure", 1):
        with pytest.raises(ValueError, match="label_blind"):
            compute_l2_stats({"cases": [{"headline": "h", "judge_pass": True, "label_blind": value}]})


def test_a_verdict_error_names_the_case_index():
    # The index is what a refactor of the unresolved-source pass would break, and
    # it is the only thing that makes the error actionable on a 386-case fixture.
    cases = [{"headline": "a", "judge_pass": True}, {"headline": "b", "judge_pass": "no"}]
    with pytest.raises(ValueError, match="case 1 has a non-boolean judge_pass"):
        compute_l2_stats({"cases": cases})


def test_a_non_boolean_judge_pass_raises_too():
    # judge_pass drives all four L2 numbers, including the label-free unresolved
    # check. Coerced, "false" would read as PASSED and null/0/[] as FAILED, so an
    # unresolved headline the judge really passed could be hidden per-case with
    # the population size untouched.
    for value in (None, 0, "false", []):
        with pytest.raises(ValueError, match="judge_pass"):
            compute_l2_stats({"cases": [{"headline": "h", "judge_pass": value, "articles": []}]})


def test_compute_l2_stats_scores_against_label_blind_not_label_pass():
    # THE DEFECT. label_pass is a copy of the judge's own verdict, so scoring
    # against it is an identity: agreement 1.0 whatever the judge did. Only
    # label_blind is independent, and here it disagrees.
    golden = {
        "cases": [
            {"headline": "h", "judge_pass": True, "label_pass": True, "label_blind": False},
        ]
    }
    stats = compute_l2_stats(golden)
    assert stats.agreement_rate == 0.0
    assert stats.leak_count == 1


def test_compute_l2_stats_excludes_cases_with_no_blind_label():
    # The judge-mirrored majority must contribute NOTHING -- not agreement, not
    # denominator. Two mirrored cases either side of one blind disagreement must
    # not dilute it from 0/1 to 2/3.
    golden = {
        "cases": [
            {"headline": "mirrored-a", "judge_pass": True, "label_pass": True},
            {"headline": "blind", "judge_pass": True, "label_pass": True, "label_blind": False},
            {"headline": "mirrored-b", "judge_pass": False, "label_pass": False},
        ]
    }
    stats = compute_l2_stats(golden)
    assert stats.total_cases == 3
    assert stats.blind_labeled_cases == 1
    assert stats.agreement_rate == 0.0


def test_compute_l2_stats_with_no_blind_labels_reports_zero_not_a_clean_score():
    golden = {"cases": [{"headline": "h", "judge_pass": True, "label_pass": True}]}
    stats = compute_l2_stats(golden)
    assert stats.blind_labeled_cases == 0
    # n=0 makes agreement_rate vacuously 1.0; the gate must catch this via the
    # subset-size guard, never by trusting the rate.
    result = compare(_metrics(l2=_l2(total=1, blind=1)), _metrics(l2=stats.to_dict()))
    assert not result.passed
    assert any("blind-labelled cases dropped" in r for r in result.regressions)


# --------------------------------------------------------------------------- #
# The committed golden set: the numbers the gate actually watches
# --------------------------------------------------------------------------- #


def test_committed_golden_set_l2_stats_are_the_blind_subset():
    stats = compute_l2_stats(load_json(GOLDEN_PATH))
    assert stats.total_cases == 386
    assert stats.blind_labeled_cases == 60
    assert stats.unresolved_sources == 32
    assert stats.unresolved_sources_passed == 0
    # 58/60 blind agreement, 33/35 fail-precision, 0 leaks. Not 1.0: the point.
    assert stats.agreement_rate == pytest.approx(58 / 60)
    assert stats.fail_precision == pytest.approx(33 / 35)
    assert stats.leak_count == 0


def test_committed_golden_set_label_pass_is_not_independent_evidence():
    # Documents WHY the blind subset is the only scored population: label_pass
    # equals judge_pass on every one of the 386 cases, so any statistic built
    # from that pair is arithmetic, not evidence.
    cases = load_json(GOLDEN_PATH)["cases"]
    assert sum(1 for c in cases if c["judge_pass"] is c["label_pass"]) == len(cases)


def test_the_unscored_majority_carries_templated_rationales():
    # Second, independent reason the 326 are not evidence: 320 of them open with
    # one of three identical 200-character preambles, differing only in the tail
    # that names the article ids. The top-3 coverage is what is stable -- it is
    # 320 at every prefix from 40 to 200 characters, while the stem COUNT moves
    # (6, 7, 9). This says nothing about the QUALITY of the 60 blind rationales;
    # distinct strings are not proof of reasoning.
    unscored = [c for c in load_json(GOLDEN_PATH)["cases"] if "label_blind" not in c]
    assert len(unscored) == 326
    for window in (40, 100, 200):
        stems = Counter(c.get("label_rationale", "")[:window] for c in unscored)
        assert sum(n for _, n in stems.most_common(3)) == 320


def test_the_two_blind_disagreements_are_proxy_artifacts_not_judge_errors():
    # The 2/60 headroom is NOT an error budget to close. Both disagreements are
    # judge-fail / blind-faithful, and both blind rationales say the specific the
    # judge failed on ("$2.25m", "about 20") is simply unverifiable from the
    # title-level proxy, resolved toward faithful by an explicit floor rule.
    # Relaxing coherence.md to pass unsupported specifics would drive agreement
    # to 60/60 and fail-precision to 1.0 and read as an improvement. If a
    # re-certification changes the character of these disagreements, this test
    # breaks and forces someone to look before trusting the direction of travel.
    cases = load_json(GOLDEN_PATH)["cases"]
    disagreements = [c for c in cases if "label_blind" in c and c["label_blind"] != c["judge_pass"]]
    assert len(disagreements) == 2
    for case in disagreements:
        assert case["label_blind"] is True and case["judge_pass"] is False
        assert "floor" in case["label_blind_rationale"].lower()


def test_unresolved_cited_sources_are_label_free_ground_truth():
    # 32 cases cite article_ids that resolve to nothing. _meta.articles_field:
    # "that non-resolution is itself the faithfulness defect". No labeller is
    # needed to say those must fail, and none of the 32 carries a blind label --
    # so this is signal the blind subset alone would miss.
    cases = load_json(GOLDEN_PATH)["cases"]
    unresolved = [c for c in cases if not c.get("articles")]
    assert len(unresolved) == 32
    assert not any("label_blind" in c for c in unresolved)
    stats = compute_l2_stats({"cases": cases})
    assert stats.unresolved_sources == 32
    assert stats.unresolved_sources_passed == 0


def test_emptying_the_unresolved_population_is_a_regression():
    # unresolved_sources_passed is an ABSOLUTE count, so resolving the 32 cases
    # away would leave it at 0 forever and disarm the label-free check without
    # tripping anything. The population size is gated for the same reason
    # blind_labeled_cases is.
    base = _metrics(l2=_l2(unresolved=32))
    cur = _metrics(l2=_l2(unresolved=0))
    result = compare(base, cur)
    assert not result.passed
    assert any("unresolved-source cases dropped" in r for r in result.regressions)


def test_a_passed_headline_citing_nothing_is_a_regression():
    baseline = load_json(BASELINE_PATH)
    golden = copy.deepcopy(load_json(GOLDEN_PATH))
    next(c for c in golden["cases"] if not c.get("articles"))["judge_pass"] = True

    stats = compute_l2_stats(golden)
    assert stats.unresolved_sources_passed == 1
    current = {**compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH), "l2_judge_agreement": stats.to_dict()}
    result = compare(baseline, current)
    assert not result.passed
    assert any("cited nothing" in r for r in result.regressions)


def test_committed_baseline_matches_the_committed_fixtures():
    current = compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH)
    assert compare(load_json(BASELINE_PATH), current).passed


def test_gate_fails_when_a_blind_label_flips_to_a_leak():
    # The whole defect was a statistic that could not move. Prove it moves: flip
    # one blind label under a headline the judge PASSED and the gate must trip.
    baseline = load_json(BASELINE_PATH)
    golden = copy.deepcopy(load_json(GOLDEN_PATH))
    leaked = next(c for c in golden["cases"] if "label_blind" in c and c["judge_pass"])
    leaked["label_blind"] = False

    stats = compute_l2_stats(golden)
    assert stats.leak_count == 1
    assert stats.agreement_rate < baseline["l2_judge_agreement"]["agreement_rate"]

    current = {**compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH), "l2_judge_agreement": stats.to_dict()}
    result = compare(baseline, current)
    assert not result.passed
    assert any("leak count rose" in r for r in result.regressions)


def test_gate_fails_when_a_blind_label_flips_to_a_false_fail():
    # The other direction, and at the smallest possible dose: ONE more headline
    # the judge failed that the blind label calls faithful. 33/35 -> 32/35 must
    # trip; a gate that only notices a wholesale flip is not much of a gate.
    baseline = load_json(BASELINE_PATH)
    golden = copy.deepcopy(load_json(GOLDEN_PATH))
    over_dropped = next(
        c for c in golden["cases"] if "label_blind" in c and not c["judge_pass"] and not c["label_blind"]
    )
    over_dropped["label_blind"] = True

    stats = compute_l2_stats(golden)
    assert stats.fail_precision == pytest.approx(32 / 35)
    current = {**compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH), "l2_judge_agreement": stats.to_dict()}
    result = compare(baseline, current)
    assert not result.passed
    assert any("fail-precision dropped" in r for r in result.regressions)


# --------------------------------------------------------------------------- #
# Broken instrument vs real regression: exit 2, not exit 1
# --------------------------------------------------------------------------- #


def test_a_malformed_golden_fixture_is_a_fixture_error_not_a_regression(tmp_path):
    # The non-boolean-label_blind guard fires during metric COMPUTATION, before
    # any baseline comparison. Left bare it would surface as exit 1, which is
    # the gate's code for "the thing being measured got worse".
    golden = copy.deepcopy(load_json(GOLDEN_PATH))
    next(c for c in golden["cases"] if "label_blind" in c)["label_blind"] = "unsure"
    bad = tmp_path / "golden.json"
    bad.write_text(json.dumps(golden), encoding="utf-8")

    with pytest.raises(FixtureError, match="unusable"):
        compute_metrics_for_gate(bad, SELECTIONS_PATH)


def test_a_stale_baseline_is_a_fixture_error_not_a_regression():
    stale = {**load_json(BASELINE_PATH)}
    stale["l2_judge_agreement"] = {"labeled_cases": 386, "agreement_rate": 1.0, "leak_count": 0}
    current = compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH)

    with pytest.raises(FixtureError, match="baseline is unreadable"):
        compare_for_gate(stale, current)


def test_a_corrupt_baseline_is_a_fixture_error_not_a_regression(tmp_path):
    # The baseline is read separately from the fixtures. Truncated JSON here used
    # to escape as a bare JSONDecodeError at exit 1 -- and the recovery the gate
    # prints (--update) is the very command that would crash.
    bad = tmp_path / "eval_baseline.json"
    bad.write_text('{"l2_judge_agreement": {"total_ca', encoding="utf-8")
    with pytest.raises(FixtureError, match="unreadable"):
        load_json_for_gate(bad)


def test_a_fixture_with_the_wrong_top_level_type_is_a_fixture_error(tmp_path):
    bad = tmp_path / "golden.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(FixtureError, match="not a JSON object"):
        compute_metrics_for_gate(bad, SELECTIONS_PATH)


def test_a_non_utf8_fixture_is_a_fixture_error(tmp_path):
    # read_text raises UnicodeDecodeError, which is a ValueError, not an OSError.
    bad = tmp_path / "eval_baseline.json"
    bad.write_bytes(b"\x80\x81\x82 not utf-8")
    with pytest.raises(FixtureError, match="unreadable"):
        load_json_for_gate(bad)


def test_a_baseline_with_a_wrong_typed_nested_value_is_a_fixture_error():
    current = compute_metrics_from_paths(GOLDEN_PATH, SELECTIONS_PATH)
    for baseline in (
        {**load_json(BASELINE_PATH), "l2_judge_agreement": []},
        {**load_json(BASELINE_PATH), "l1_golden_headlines": None},
    ):
        with pytest.raises(FixtureError, match="baseline is unreadable"):
            compare_for_gate(baseline, current)


def test_a_real_regression_is_not_a_fixture_error():
    # The other side of the same policy: a genuine regression must come back as
    # a result, never as an exception, or the gate would exit 2 and read as a
    # broken instrument.
    result = compare_for_gate(_metrics(l2=_l2(leak_count=0)), _metrics(l2=_l2(leak_count=3)))
    assert not result.passed


def test_golden_headlines_projection_is_selections_shaped():
    golden = {"cases": [{"headline": "first"}, {"headline": "second"}, {"headline": "third"}]}
    sel = golden_headlines_as_selections(golden)
    assert sel["must_know"][0]["headline"] == "first"
    assert [s["headline"] for s in sel["should_know"]] == ["second", "third"]
    assert isinstance(sel["preheader"], str)
