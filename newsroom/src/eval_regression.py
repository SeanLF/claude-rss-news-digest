"""Offline regression gate for the eval floor (L1 graders + L2 judge agreement).

Recomputes the eval metrics that ``bin/eval`` (L1 code assertions) and
``bin/eval-judge`` (L2 trust-chain agreement) measure, but as a *gate*: it
compares the current numbers against a committed known-good baseline
(``newsroom/tests/fixtures/eval_baseline.json``) and reports a regression when

  * an L1 check that passed at baseline now fails (pass-rate drop), or
  * the L2 judge fail-precision drops below baseline, or
  * the L2 leak count (false passes) rises above baseline.

Pure code + fixtures, no model calls and no credit -- safe to run on every
change and in CI. ``bin/eval-regression`` is the thin CLI; this module holds the
metric computation and comparison logic so the unit tests exercise the same code.

The metrics are computed over two committed fixtures:

  * ``coherence_golden.json`` -- the 386-case COHERENCE golden set. Its headlines
    feed the L1 graders (a "selections"-shaped view); its judge_pass/label_pass
    pairs feed ``eval_judge.score_agreement`` for the L2 stats.
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


def l1_check_map(report: GradeReport) -> dict[str, bool]:
    """name -> passed for every L1 check (order-independent comparison)."""
    return {c.name: c.passed for c in report.checks}


@dataclass(frozen=True)
class L2Stats:
    """The three L2 numbers the gate watches, plus context."""

    labeled_cases: int
    agreement_rate: float
    fail_precision: float | None
    leak_count: int  # false passes (judge pass, label unfaithful)

    def to_dict(self) -> dict:
        return {
            "labeled_cases": self.labeled_cases,
            "agreement_rate": round(self.agreement_rate, 6),
            "fail_precision": None if self.fail_precision is None else round(self.fail_precision, 6),
            "leak_count": self.leak_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> L2Stats:
        return cls(
            labeled_cases=d["labeled_cases"],
            agreement_rate=d["agreement_rate"],
            fail_precision=d.get("fail_precision"),
            leak_count=d["leak_count"],
        )


def _disambiguated(cases: list[dict]) -> tuple[list[JudgeCase], list[LabeledCase]]:
    """Build judge/label lists, suffixing headlines by index.

    ``score_agreement`` joins judge to label by headline, but the golden set has
    a duplicate headline carrying *different* verdicts. A plain join would drop
    one case. Suffixing with the row index on BOTH sides keeps the pairing exact
    and preserves all cases (n == len(cases)).
    """
    judge: list[JudgeCase] = []
    label: list[LabeledCase] = []
    for i, c in enumerate(cases):
        key = f"{c['headline']}␟{i}"  # symbol-for-unit-separator; never collides
        judge.append(JudgeCase(headline=key, articles=[], judge_pass=bool(c["judge_pass"]), judge_reason=""))
        label.append(LabeledCase(headline=key, label_pass=bool(c["label_pass"]), label_rationale=""))
    return judge, label


def compute_l2_stats(golden: dict) -> L2Stats:
    """Run ``score_agreement`` over the golden judge_pass/label_pass pairs."""
    judge, label = _disambiguated(golden.get("cases", []))
    report = score_agreement(judge, label)
    return L2Stats(
        labeled_cases=report.n,
        agreement_rate=report.agreement_rate,
        fail_precision=report.fail_precision,
        leak_count=report.pass_unfaithful,
    )


def compute_metrics(golden: dict, selections: dict, *, limits: GraderLimits | None = None) -> dict:
    """Compute the full metric payload (the baseline file's shape).

    ``golden``     : parsed coherence_golden.json ({_meta, cases:[...]}).
    ``selections`` : a representative parsed selections.json fixture.
    """
    limits = limits or GraderLimits()
    golden_report = grade_selections(golden_headlines_as_selections(golden), limits=limits)
    selections_report = grade_selections(selections, limits=limits)
    return {
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
    # Fewer labeled cases than baseline means the golden set shrank or failed to
    # load -- a hard regression. Without this, an empty/degraded set yields n=0,
    # agreement_rate 1.0 and leak_count 0, which would otherwise pass clean (a
    # hollow gate). Guards the "regression gate silently passes when computation
    # is empty" failure mode.
    if current.labeled_cases < baseline.labeled_cases:
        result.regressions.append(
            f"[l2] labeled cases dropped: {baseline.labeled_cases} -> {current.labeled_cases} "
            "(golden set shrank or failed to load)"
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


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compute_metrics_from_paths(golden_path: str | Path, selections_path: str | Path) -> dict:
    return compute_metrics(load_json(golden_path), load_json(selections_path))
