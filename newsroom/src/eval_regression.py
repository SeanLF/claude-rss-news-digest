"""Offline regression gate for the eval floor (L1 graders + L2 judge agreement).

Recomputes the eval metrics that ``bin/eval`` (L1 code assertions) and
``bin/eval-judge`` (L2 trust-chain agreement) measure, but as a *gate*: it
compares the current numbers against a committed known-good baseline
(``newsroom/tests/fixtures/eval_baseline.json``) and reports a regression when

  * an L1 check that passed at baseline now fails (pass-rate drop), or
  * the L1 checks grade fewer golden headlines than at baseline, or
  * the recorded L2 agreement rate or fail-precision drops below baseline, or
  * the recorded L2 leak count (false passes) rises above baseline, or
  * a headline citing nothing resolvable is recorded as passed, or
  * the golden set, its blind-labelled subset, or its unresolved-source subset
    shrinks below baseline.

SCOPE -- what the L2 arm does NOT do
    It makes no model calls, so it never observes the live judge. ``judge_pass``
    is frozen in the fixture; editing ``.claude/agents/coherence.md`` changes
    nothing here. The L2 numbers move only when the golden set is re-judged and
    re-committed (the re-certification described in its ``_meta``), or when the
    fixture is otherwise edited.

Pure code + fixtures, no model calls and no credit -- safe to run on every
change, though nothing runs it automatically: its only caller is ``make eval``,
not ``bin/ci``. ``bin/eval-regression`` is the thin CLI; this module holds the
metric computation and comparison logic so the unit tests exercise the same code.

The metrics are computed over two committed fixtures:

  * ``coherence_golden.json`` -- the COHERENCE golden set. Its headlines feed
    the L1 graders (a "selections"-shaped view); the cases carrying a
    ``label_blind`` verdict feed ``eval_judge.score_agreement`` for the L2 stats.
    ``label_pass`` is deliberately NOT scored: it equals ``judge_pass`` on every
    case (the fixture's own ``_meta`` says so), so agreement against it is an
    identity pinned at its ceiling. ``label_blind`` was decided from ``articles[]``
    alone, without reading the judge -- independent OF THE JUDGE'S VERDICT, which
    is not the same as human-labelled; the labeller is unrecorded. The one
    label-free signal is ``unresolved_sources_passed``.
  * a representative ``selections.json`` fixture -- graded by L1 as-is. The
    committed real-output fixture is old-schema, so some L1 checks legitimately
    fail; the gate only flags a *regression* (a check flipping pass->fail), never
    the absolute state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from eval_graders import GradeReport, GraderLimits, grade_selections
from eval_judge import JudgeCase, LabeledCase, score_agreement

# --------------------------------------------------------------------------- #
# Metric computation
# --------------------------------------------------------------------------- #


def golden_headlines_as_selections(golden: dict) -> dict:
    """Project the golden coherence cases into a selections-shaped dict.

    Only headlines are real in the golden set, so the other story fields are
    placeholders sufficient to exercise the L1 graders deterministically. The
    headlines land in ``should_know`` (the high-volume tier) plus one in
    ``must_know`` so required-field/count checks have something to grade.
    """
    headlines = [c["headline"] for c in golden.get("cases", []) if c.get("headline")]

    def _story(headline: str) -> dict:
        return {
            "headline": headline,
            "summary": "placeholder summary",
            "why_it_matters": "placeholder rationale",
            "sources": [{"article_id": "A1"}],
        }

    return {
        "must_know": [_story(headlines[0])] if headlines else [],
        "should_know": [_story(h) for h in headlines[1:]],
        "preheader": "golden-set L1 projection",
    }


def _story_count(selections: dict) -> int:
    """How many stories the L1 checks actually graded.

    The projection above drops cases with a falsy headline, so a field rename or
    extraction bug can empty it while every case record survives. Ten of the
    eleven L1 checks then report PASS over zero stories -- vacuous truth, the
    same hole the L2 population guards close. Gated in ``compare``.
    """
    return len(selections.get("must_know", [])) + len(selections.get("should_know", []))


def l1_check_map(report: GradeReport) -> dict[str, bool]:
    """name -> passed for every L1 check (order-independent comparison)."""
    return {c.name: c.passed for c in report.checks}


@dataclass(frozen=True)
class L2Stats:
    """The L2 numbers the gate watches.

    ``total_cases`` counts the whole golden set; the rates are computed over
    ``blind_labeled_cases`` only (spelled the US way to match ``eval_judge``'s
    ``labeled``/``unlabeled``). Both counts are reported so a shrinking evidence
    base is visible next to the rate it would inflate.
    """

    total_cases: int
    blind_labeled_cases: int
    agreement_rate: float
    fail_precision: float | None
    leak_count: int  # false passes (judge pass, blind label unfaithful)
    unresolved_sources: int  # cases whose cited article_ids resolved to nothing
    unresolved_sources_passed: int  # ... of those, ones the judge passed anyway

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "blind_labeled_cases": self.blind_labeled_cases,
            "agreement_rate": round(self.agreement_rate, 6),
            "fail_precision": None if self.fail_precision is None else round(self.fail_precision, 6),
            "leak_count": self.leak_count,
            "unresolved_sources": self.unresolved_sources,
            "unresolved_sources_passed": self.unresolved_sources_passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> L2Stats:
        # fail_precision is nullable but its KEY is required: absent, .get() would
        # read as None and silently disable the fail-precision comparison.
        missing = {
            "total_cases",
            "blind_labeled_cases",
            "agreement_rate",
            "fail_precision",
            "leak_count",
            "unresolved_sources",
            "unresolved_sources_passed",
        } - d.keys()
        if missing:
            raise ValueError(
                f"l2_judge_agreement is missing {sorted(missing)}; this baseline predates the "
                "label_blind-scored L2 arm. Regenerate it with: bin/eval-regression --update"
            )
        return cls(
            total_cases=d["total_cases"],
            blind_labeled_cases=d["blind_labeled_cases"],
            agreement_rate=d["agreement_rate"],
            fail_precision=d["fail_precision"],
            leak_count=d["leak_count"],
            unresolved_sources=d["unresolved_sources"],
            unresolved_sources_passed=d["unresolved_sources_passed"],
        )


def _verdict(case: dict, key: str, index: int) -> bool:
    """Read a pass/fail field, refusing to coerce anything that is not a boolean.

    bool() would read null/0/[] as FAIL and "false"/"unsure" as PASS -- a
    fabricated verdict that moves every L2 number, including the label-free
    unresolved-source check.
    """
    value = case[key]
    if not isinstance(value, bool):
        hint = " To record 'undecided', omit the field." if key == "label_blind" else ""
        raise ValueError(f"case {index} has a non-boolean {key} ({value!r}); a verdict is true or false.{hint}")
    return value


def _disambiguated(cases: list[dict]) -> tuple[list[JudgeCase], list[LabeledCase]]:
    """Build judge/label lists, suffixing headlines by index.

    ``score_agreement`` joins judge to label by headline, but the golden set has
    a duplicate headline carrying *different* verdicts. A plain join would drop
    one case. Suffixing with the row index on BOTH sides keeps the pairing exact.

    Only cases carrying ``label_blind`` get a label; the rest arrive at
    ``score_agreement`` unlabelled and are excluded from the matrix entirely --
    they contribute neither agreement nor denominator.
    """
    judge: list[JudgeCase] = []
    label: list[LabeledCase] = []
    for i, c in enumerate(cases):
        key = f"{c['headline']}␟{i}"  # symbol-for-unit-separator; never collides
        judge.append(JudgeCase(headline=key, articles=[], judge_pass=_verdict(c, "judge_pass", i), judge_reason=""))
        if "label_blind" in c:
            label.append(LabeledCase(headline=key, label_pass=_verdict(c, "label_blind", i), label_rationale=""))
    return judge, label


def compute_l2_stats(golden: dict) -> L2Stats:
    """Score the judge against the blind labels, over the blind subset only."""
    cases = golden.get("cases", [])
    judge, label = _disambiguated(cases)
    report = score_agreement(judge, label)
    unresolved = [(i, c) for i, c in enumerate(cases) if not c.get("articles")]
    return L2Stats(
        total_cases=len(cases),
        blind_labeled_cases=report.n,
        agreement_rate=report.agreement_rate,
        fail_precision=report.fail_precision,
        leak_count=report.pass_unfaithful,
        unresolved_sources=len(unresolved),
        unresolved_sources_passed=sum(1 for i, c in unresolved if _verdict(c, "judge_pass", i)),
    )


def compute_metrics(golden: dict, selections: dict, *, limits: GraderLimits | None = None) -> dict:
    """Compute the full metric payload (the baseline file's shape).

    ``golden``     : parsed coherence_golden.json ({_meta, cases:[...]}).
    ``selections`` : a representative parsed selections.json fixture.
    """
    limits = limits or GraderLimits()
    golden_selections = golden_headlines_as_selections(golden)
    golden_report = grade_selections(golden_selections, limits=limits)
    selections_report = grade_selections(selections, limits=limits)
    return {
        "l1_golden_stories": _story_count(golden_selections),
        "l1_golden_headlines": l1_check_map(golden_report),
        "l1_selections_fixture": l1_check_map(selections_report),
        "l2_judge_agreement": compute_l2_stats(golden).to_dict(),
    }


# --------------------------------------------------------------------------- #
# Comparison (baseline vs current)
# --------------------------------------------------------------------------- #


@dataclass
class RegressionResult:
    """Outcome of comparing current metrics against a baseline."""

    regressions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # non-fatal observations (improvements, new checks)

    @property
    def passed(self) -> bool:
        return not self.regressions


def _compare_l1(name: str, baseline: dict[str, bool], current: dict[str, bool], result: RegressionResult) -> None:
    """Flag any check that passed at baseline but fails now (a pass-rate drop)."""
    for check, was_passing in baseline.items():
        if check not in current:
            result.notes.append(f"[{name}] check '{check}' present at baseline but absent now")
            continue
        now_passing = current[check]
        if was_passing and not now_passing:
            result.regressions.append(f"[{name}] check '{check}' regressed: PASS -> FAIL")
        elif not was_passing and now_passing:
            result.notes.append(f"[{name}] check '{check}' improved: FAIL -> PASS (update baseline)")
    for check in current.keys() - baseline.keys():
        result.notes.append(f"[{name}] new check '{check}' not in baseline (update baseline)")


def _compare_l2(baseline: L2Stats, current: L2Stats, result: RegressionResult) -> None:
    # Fewer cases than baseline means the golden set shrank or failed to load --
    # a hard regression. Without this, an empty/degraded set yields n=0,
    # agreement_rate 1.0 and leak_count 0, which would otherwise pass clean (a
    # hollow gate). Guards the "regression gate silently passes when computation
    # is empty" failure mode.
    if current.total_cases < baseline.total_cases:
        result.regressions.append(
            f"[l2] golden cases dropped: {baseline.total_cases} -> {current.total_cases} "
            "(golden set shrank or failed to load)"
        )

    # The same hole one level in: the rates are computed over the blind subset,
    # so deleting blind labels shrinks the evidence while leaving every rate
    # looking clean (at n=0 agreement_rate is vacuously 1.0).
    if current.blind_labeled_cases < baseline.blind_labeled_cases:
        result.regressions.append(
            f"[l2] blind-labelled cases dropped: {baseline.blind_labeled_cases} -> "
            f"{current.blind_labeled_cases} (the independent evidence base shrank)"
        )
    elif current.blind_labeled_cases > baseline.blind_labeled_cases:
        result.notes.append(
            f"[l2] blind-labelled cases grew: {baseline.blind_labeled_cases} -> "
            f"{current.blind_labeled_cases} (update baseline)"
        )

    # Agreement rate falling is a hard regression. It was recorded but never
    # compared before, so a re-certified fixture could disagree with the blind
    # labels more often and still pass the gate untouched.
    if current.agreement_rate < baseline.agreement_rate - 1e-9:
        result.regressions.append(
            f"[l2] agreement rate dropped: {baseline.agreement_rate:.4f} -> {current.agreement_rate:.4f}"
        )
    elif current.agreement_rate > baseline.agreement_rate + 1e-9:
        result.notes.append(
            f"[l2] agreement rate improved: {baseline.agreement_rate:.4f} -> {current.agreement_rate:.4f}"
        )

    # The one L2 check that needs no labeller at all: a headline whose cited
    # article_ids resolve to nothing cannot be faithful to them, so the judge
    # must fail it. Model-free ground truth, and none of those cases carries a
    # blind label -- signal the blind subset alone would miss.
    if current.unresolved_sources < baseline.unresolved_sources:
        result.regressions.append(
            f"[l2] unresolved-source cases dropped: {baseline.unresolved_sources} -> "
            f"{current.unresolved_sources} (this evidence base shrank; the count below is "
            "absolute, so emptying the population disarms it)"
        )
    elif current.unresolved_sources > baseline.unresolved_sources:
        result.notes.append(
            f"[l2] unresolved-source cases grew: {baseline.unresolved_sources} -> "
            f"{current.unresolved_sources} (update baseline)"
        )
    if current.unresolved_sources_passed > baseline.unresolved_sources_passed:
        result.regressions.append(
            f"[l2] headlines passed that cited nothing resolvable: "
            f"{baseline.unresolved_sources_passed} -> {current.unresolved_sources_passed}"
        )

    # Leak count rising is a hard regression.
    if current.leak_count > baseline.leak_count:
        result.regressions.append(
            f"[l2] leak count rose: {baseline.leak_count} -> {current.leak_count} (more false passes)"
        )
    elif current.leak_count < baseline.leak_count:
        result.notes.append(f"[l2] leak count improved: {baseline.leak_count} -> {current.leak_count}")

    # Fail-precision dropping is a hard regression. None means "undefined"
    # (judge failed nothing); treat a defined baseline going None as a regression.
    base_fp, cur_fp = baseline.fail_precision, current.fail_precision
    if base_fp is not None:
        if cur_fp is None:
            result.regressions.append("[l2] fail-precision became undefined (judge stopped failing cases)")
        elif cur_fp < base_fp - 1e-9:
            result.regressions.append(f"[l2] fail-precision dropped: {base_fp:.4f} -> {cur_fp:.4f}")
        elif cur_fp > base_fp + 1e-9:
            result.notes.append(f"[l2] fail-precision improved: {base_fp:.4f} -> {cur_fp:.4f}")


def compare(baseline: dict, current: dict) -> RegressionResult:
    """Compare a current metric payload against a baseline payload."""
    result = RegressionResult()
    base_stories, cur_stories = baseline["l1_golden_stories"], current["l1_golden_stories"]
    if cur_stories < base_stories:
        result.regressions.append(
            f"[l1] graded stories dropped: {base_stories} -> {cur_stories} "
            "(the L1 checks below are passing over fewer headlines than at baseline)"
        )
    elif cur_stories > base_stories:
        result.notes.append(f"[l1] graded stories grew: {base_stories} -> {cur_stories} (update baseline)")
    _compare_l1("l1_golden_headlines", baseline["l1_golden_headlines"], current["l1_golden_headlines"], result)
    _compare_l1("l1_selections_fixture", baseline["l1_selections_fixture"], current["l1_selections_fixture"], result)
    _compare_l2(
        L2Stats.from_dict(baseline["l2_judge_agreement"]),
        L2Stats.from_dict(current["l2_judge_agreement"]),
        result,
    )
    return result


# --------------------------------------------------------------------------- #
# IO helpers (used by bin/eval-regression and the baseline generator)
# --------------------------------------------------------------------------- #


class FixtureError(Exception):
    """A fixture or baseline is missing, malformed or stale-shaped.

    Distinct from a regression: the gate exits 2 for this and 1 for a real
    regression, so CI can tell "the instrument is broken" from "the thing being
    measured got worse".
    """


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compute_metrics_from_paths(golden_path: str | Path, selections_path: str | Path) -> dict:
    return compute_metrics(load_json(golden_path), load_json(selections_path))


def load_json_for_gate(path: str | Path) -> dict:
    """Read a fixture, raising FixtureError for anything that is not a JSON object."""
    try:
        data = load_json(path)
    except (ValueError, OSError) as exc:
        # ValueError covers both JSONDecodeError and the UnicodeDecodeError a
        # non-UTF-8 file raises out of read_text; neither is an OSError.
        raise FixtureError(f"{Path(path).name} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise FixtureError(f"{Path(path).name} is not a JSON object (got {type(data).__name__})")
    return data


def compute_metrics_for_gate(golden_path: str | Path, selections_path: str | Path) -> dict:
    golden = load_json_for_gate(golden_path)
    selections = load_json_for_gate(selections_path)
    try:
        return compute_metrics(golden, selections)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FixtureError(f"fixtures are unusable: {exc}") from exc


def compare_for_gate(baseline: dict, current: dict) -> RegressionResult:
    try:
        return compare(baseline, current)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        # Same tuple as compute_metrics_for_gate: a nested value of the wrong
        # type raises AttributeError, which as a bare traceback exits 1 -- the
        # code reserved for "the thing being measured got worse".
        raise FixtureError(f"baseline is unreadable: {exc}") from exc
