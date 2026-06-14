"""L2 eval harness: validate the COHERENCE LLM-as-judge against independent labels.

L1 (``eval_graders.py``) is pure code assertions on the curated output. L2 sits
one layer up: it asks whether the COHERENCE subagent -- itself an LLM-as-judge --
is *right* when it emits ``pass: true/false`` for a headline-vs-source pairing.
The pipeline DROPS reader-facing headlines whose COHERENCE verdict is ``pass:false``
(see ``merge.py``), so a wrong judge either leaks an unfaithful headline (false
pass) or silently drops a good one (false fail). Neither has ever been measured.

The validation method is the "trust chain": an INDEPENDENT labeler judges each
headline against its source representation, then we compute agreement between
COHERENCE's verdicts and those labels, and surface the disagreements for
who's-right adjudication.

PROXY CAVEAT -- IMPORTANT
    ``article_index.json`` only carries the RSS ``original_title`` (+ source name),
    not the full article body. So the "source representation" a labeler sees here
    is a *title-level proxy*. A headline can be faithful to the title yet distort
    the body, or vice versa. Title-level labels are strictly weaker than
    full-article labels and should be read as a floor, not a verdict on the judge.

Standalone: this module does not touch the live pipeline (merge.py/run.py).

Shapes consumed (all under ``data/claude_input/``):

    coherence_report.json : {"results": [{headline, article_ids:[...], pass, reason}, ...]}
    draft_selections.json : {must_know:[...], should_know:[...], preheader}
                            (only used as a fallback source of article_ids per headline)
    article_index.json    : {article_id: {url, source_id, bias, original_title, name}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ArticleRef:
    """A source article resolved from an article_id (title-level proxy)."""

    id: str
    title: str
    source: str


@dataclass(frozen=True)
class JudgeCase:
    """One COHERENCE verdict joined to its resolved source articles.

    ``judge_pass`` is COHERENCE's verdict; ``judge_reason`` its stated rationale.
    The ``articles`` are the title-level source representations a labeler reads.
    """

    headline: str
    articles: list[ArticleRef]
    judge_pass: bool
    judge_reason: str

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "articles": [{"id": a.id, "title": a.title, "source": a.source} for a in self.articles],
            "judge_pass": self.judge_pass,
            "judge_reason": self.judge_reason,
        }


@dataclass(frozen=True)
class LabeledCase:
    """An independent label for a headline-vs-source faithfulness judgment.

    ``label_pass`` is True when the headline faithfully represents the source
    (the labeler's analogue of COHERENCE's ``pass``). Joined to a ``JudgeCase``
    by headline for scoring.
    """

    headline: str
    label_pass: bool
    label_rationale: str


# --------------------------------------------------------------------------- #
# Loading / joining
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _article_ids_by_headline(draft_selections: dict) -> dict[str, list[str]]:
    """Map each draft headline -> its source article_ids (fallback when a
    coherence result omits ``article_ids``)."""
    out: dict[str, list[str]] = {}
    for tier in ("must_know", "should_know"):
        for item in draft_selections.get(tier, []) or []:
            if not isinstance(item, dict):
                continue
            headline = item.get("headline", "")
            ids = [
                s.get("article_id")
                for s in item.get("sources", []) or []
                if isinstance(s, dict) and s.get("article_id")
            ]
            if headline:
                out[headline] = ids
    return out


def _resolve_articles(article_ids: list[str], article_index: dict) -> list[ArticleRef]:
    refs: list[ArticleRef] = []
    for aid in article_ids:
        entry = article_index.get(aid) or {}
        refs.append(
            ArticleRef(
                id=aid,
                title=entry.get("original_title", ""),
                source=entry.get("name", entry.get("source_id", "")),
            )
        )
    return refs


def build_cases(
    coherence_report: dict,
    article_index: dict,
    draft_selections: dict | None = None,
) -> list[JudgeCase]:
    """Join COHERENCE verdicts to resolved source articles (pure, no IO).

    Each coherence result supplies ``article_ids`` directly; when absent, they
    are looked up from ``draft_selections`` by headline. Unknown ids resolve to
    empty-title refs (still surfaced, so the gap is visible).
    """
    fallback_ids = _article_ids_by_headline(draft_selections or {})
    cases: list[JudgeCase] = []
    for result in coherence_report.get("results", []) or []:
        if not isinstance(result, dict):
            continue
        headline = result.get("headline", "")
        ids = result.get("article_ids")
        if not ids:
            ids = fallback_ids.get(headline, [])
        cases.append(
            JudgeCase(
                headline=headline,
                articles=_resolve_articles(list(ids), article_index),
                judge_pass=bool(result.get("pass")),
                judge_reason=result.get("reason", ""),
            )
        )
    return cases


def load_coherence_cases(claude_input_dir: str | Path) -> list[JudgeCase]:
    """Load the real intermediate files and return joined judge cases.

    Reads ``coherence_report.json`` and ``article_index.json`` (required), and
    ``draft_selections.json`` (optional, for article_id fallback).
    """
    d = Path(claude_input_dir)
    coherence_report = _read_json(d / "coherence_report.json")
    article_index = _read_json(d / "article_index.json")
    draft_path = d / "draft_selections.json"
    draft_selections = _read_json(draft_path) if draft_path.exists() else {}
    return build_cases(coherence_report, article_index, draft_selections)


def load_labels(labels_path: str | Path) -> list[LabeledCase]:
    """Load a labels JSON file: a list of {headline, label_pass, label_rationale}."""
    with Path(labels_path).open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        LabeledCase(
            headline=row["headline"],
            label_pass=bool(row["label_pass"]),
            label_rationale=row.get("label_rationale", ""),
        )
        for row in raw
    ]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Disagreement:
    """A case where the judge verdict and the independent label diverge."""

    headline: str
    judge_pass: bool
    label_pass: bool
    judge_reason: str
    label_rationale: str


@dataclass
class AgreementReport:
    """Confusion matrix and agreement stats between judge and label (binary).

    Confusion-matrix cells (judge x label):
        ``pass_faithful``     : judge pass,  label faithful   (true pass / agree)
        ``pass_unfaithful``   : judge pass,  label unfaithful (FALSE PASS -- leak)
        ``fail_faithful``     : judge fail,  label faithful   (FALSE FAIL -- wrongly dropped)
        ``fail_unfaithful``   : judge fail,  label unfaithful (true fail / agree)
    """

    pass_faithful: int = 0
    pass_unfaithful: int = 0
    fail_faithful: int = 0
    fail_unfaithful: int = 0
    unlabeled: list[str] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)

    @property
    def n(self) -> int:
        """Number of scored (labeled) cases."""
        return self.pass_faithful + self.pass_unfaithful + self.fail_faithful + self.fail_unfaithful

    @property
    def agreements(self) -> int:
        return self.pass_faithful + self.fail_unfaithful

    @property
    def agreement_rate(self) -> float:
        """Fraction of labeled cases where judge and label agree; 1.0 if none."""
        return self.agreements / self.n if self.n else 1.0

    @property
    def pass_precision(self) -> float | None:
        """Of cases the judge passed, fraction the label deems faithful.

        None when the judge passed nothing (precision undefined).
        """
        passed = self.pass_faithful + self.pass_unfaithful
        return self.pass_faithful / passed if passed else None

    @property
    def fail_precision(self) -> float | None:
        """Of cases the judge failed, fraction the label deems unfaithful.

        None when the judge failed nothing (precision undefined) -- the common
        case for an all-pass sample, where FAIL behavior is simply unvalidated.
        """
        failed = self.fail_faithful + self.fail_unfaithful
        return self.fail_unfaithful / failed if failed else None

    def render(self) -> str:
        lines = [
            "COHERENCE judge vs independent labels (L2 trust-chain)",
            "=" * 56,
            f"labeled cases : {self.n}",
            f"agreement rate: {self.agreement_rate:.0%} ({self.agreements}/{self.n})",
            "",
            "confusion matrix (judge x label):",
            "                       label:faithful  label:unfaithful",
            f"  judge:pass            {self.pass_faithful:>13}  {self.pass_unfaithful:>16}",
            f"  judge:fail            {self.fail_faithful:>13}  {self.fail_unfaithful:>16}",
            "",
            f"  false passes (leaked)        : {self.pass_unfaithful}",
            f"  false fails  (wrongly dropped): {self.fail_faithful}",
            f"  pass-precision: {self._fmt(self.pass_precision)}   fail-precision: {self._fmt(self.fail_precision)}",
        ]
        if self.unlabeled:
            lines += ["", f"unlabeled judge cases (no matching label): {len(self.unlabeled)}"]
            lines += [f"  - {h}" for h in self.unlabeled]
        if self.disagreements:
            lines += ["", "disagreements:"]
            for d in self.disagreements:
                lines += [
                    f"  - {d.headline!r}",
                    f"      judge={'pass' if d.judge_pass else 'fail'} | label={'faithful' if d.label_pass else 'unfaithful'}",
                    f"      judge_reason : {d.judge_reason}",
                    f"      label_reason : {d.label_rationale}",
                ]
        else:
            lines += ["", "disagreements: none"]
        return "\n".join(lines)

    @staticmethod
    def _fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.0%}"


def score_agreement(
    cases: list[JudgeCase],
    labels: list[LabeledCase],
) -> AgreementReport:
    """Compare judge verdicts against independent labels (binary, joined by headline).

    Args:
        cases: joined COHERENCE cases (from ``build_cases`` / ``load_coherence_cases``).
        labels: independent labels.

    Returns:
        An ``AgreementReport`` with the 2x2 confusion matrix, agreement rate,
        and the list of disagreements. Judge cases without a matching label are
        recorded in ``unlabeled`` and excluded from the matrix.
    """
    labels_by_headline = {label.headline: label for label in labels}
    report = AgreementReport()
    for case in cases:
        label = labels_by_headline.get(case.headline)
        if label is None:
            report.unlabeled.append(case.headline)
            continue
        if case.judge_pass and label.label_pass:
            report.pass_faithful += 1
        elif case.judge_pass and not label.label_pass:
            report.pass_unfaithful += 1
        elif not case.judge_pass and label.label_pass:
            report.fail_faithful += 1
        else:
            report.fail_unfaithful += 1
        if case.judge_pass != label.label_pass:
            report.disagreements.append(
                Disagreement(
                    headline=case.headline,
                    judge_pass=case.judge_pass,
                    label_pass=label.label_pass,
                    judge_reason=case.judge_reason,
                    label_rationale=label.label_rationale,
                )
            )
    return report
