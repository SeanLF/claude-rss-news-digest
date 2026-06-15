"""L2-style eval: the within-story "does why_it_matters earn its slot?" judge.

``why_it_matters`` is the one line per story that is supposed to add a dimension
beyond the summary -- a specific mechanism, contradiction, second-order
consequence, concrete stake, or contextual fact (see ``.claude/agents/write.md``).
An error-analysis pass found ~38% of production lines were *filler*: generic
significance language ("signals", "sets the tone", "affects millions") that
restates the summary's own framing without adding anything.

This module is the *metric* for that defect: a binary judge that decides whether
a single why_it_matters line adds a substantive new dimension. It is validated
against an independent human-labelled golden set (``why_judge_golden.json``) the
same way the COHERENCE judge is (``eval_judge.py``): compute agreement between
the judge's verdict and the labels, and surface the disagreements.

It exists to be a GEPA optimisation target (drive down the WRITE filler rate),
NOT the WRITE fix itself.

Two surfaces:
  * ``WHY_JUDGE_PROMPT`` + ``judge_why`` -- the live judge (one structured call
    per line, via the Agent SDK wrapper so it shares the pipeline's auth).
  * ``score_agreement`` + ``WhyAgreementReport`` -- pure-code offline scoring of
    judge verdicts against labels (no model calls; used by the tests / gate).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# The validated judge prompt (v2). See why_judge_golden.json _meta for the
# v1->v2 evolution and validation numbers (agreement 0.867 on 45 cases).
# --------------------------------------------------------------------------- #

WHY_JUDGE_PROMPT = """You are an editor judging whether a news item's "why_it_matters" line earns its slot.

Given a headline, a summary, and a why_it_matters line, decide ONE thing:
Does why_it_matters add at least one NEW dimension beyond the summary?

METHOD (apply literally):
1. Mentally strip the significance-verbs and importance-phrases: "signals", "marks", "spotlights", "sets the tone", "represents", "raises stakes", "positioning", "reshaping", "affects millions", "underscores", "highlights", "could reshape".
2. Then ask: does any CONCRETE new element remain that is NOT already stated, and is NOT trivially inferable, from the summary? A new element is ANY of:
   - a new fact, actor, named action, date, number, or prior event (e.g. "2026 elections", "Trump threatens strikes", "most severe unrest in years"),
   - a specific mechanism or cause-and-effect chain,
   - a contradiction, irony, or tension,
   - a contagion / parallel / spillover implication (e.g. "could prompt parallel decisions from Sweden and the Baltics"),
   - an interpretive REFRAME that recasts the story at a different level (e.g. a local accident reframed as a Belt-and-Road regional-safety story; an injunction reframed as setting a legal precedent; a benchmark gap reframed as benchmark-optimised-vs-real-capability).

If at least one such new element survives the strip, adds_dimension = TRUE -- EVEN IF the line also contains generic significance language. The mere presence of significance verbs does NOT make a line filler.

adds_dimension = FALSE (filler) ONLY when, after stripping the significance-verbs, NOTHING new remains: the line just re-labels the summary's own facts or framing in vaguer importance language, adding no new fact, mechanism, frame, or implication.

Do NOT demand a fully-developed mechanism. A single concrete new fact, or a genuine reframe, is enough to earn the slot. Reserve "filler" for lines that are essentially pure restatement.

Respond with ONLY a JSON object, no prose around it:
{"adds_dimension": true|false, "reason": "<name the surviving new element, or the generic phrase that makes it filler>"}"""

JUDGE_MODEL = "claude-sonnet-4-6"


def _build_user_prompt(headline: str, summary: str, why_it_matters: str) -> str:
    return f"headline: {headline}\n\nsummary: {summary}\n\nwhy_it_matters: {why_it_matters}"


def _parse_verdict(text: str) -> tuple[bool, str]:
    """Extract {adds_dimension, reason} from the judge's text output.

    The Agent SDK path returns free text, so we scan for the first JSON object
    that actually parses AND carries ``adds_dimension``. A plain greedy
    ``{.*}`` match is wrong here: the prompt ends with a literal schema example
    (``{"adds_dimension": true|false, ...}``), and a model that echoes it would
    make a greedy span run from that brace to the real answer's closing brace --
    invalid JSON. ``raw_decode`` from each ``{`` skips non-JSON fragments (the
    schema line fails to parse on ``true|false``) and stops at the real verdict.
    Raises ValueError if none is found (fail loud -- a silently-defaulted
    verdict would corrupt the metric).
    """
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "adds_dimension" in obj:
            return bool(obj["adds_dimension"]), str(obj.get("reason", ""))
    raise ValueError(f"why-judge returned no JSON object with adds_dimension: {text[:200]!r}")


def judge_why(
    headline: str,
    summary: str,
    why_it_matters: str,
    *,
    model: str = JUDGE_MODEL,
    timeout: int = 60,
) -> tuple[bool, str]:
    """Live judge: returns (adds_dimension, reason) for one why_it_matters line.

    Routed through the Agent SDK wrapper (``claude_cli.run_sync``) so it shares
    the pipeline's OAuth/credit auth rather than needing ANTHROPIC_API_KEY.
    """
    import claude_cli  # local import: keeps the pure-code scoring path dependency-free

    text = claude_cli.run_sync(
        _build_user_prompt(headline, summary, why_it_matters),
        model=model,
        system_prompt=WHY_JUDGE_PROMPT,
        max_turns=1,
        timeout=timeout,
    )
    return _parse_verdict(text)


# --------------------------------------------------------------------------- #
# Offline scoring (pure code, no model calls).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WhyCase:
    """One why_it_matters line joined to its judge verdict and human label.

    ``filler`` is the positive class we care about detecting (the inverse of
    ``adds_dimension``): a filler line that the judge passes is a missed defect.
    """

    headline: str
    summary: str
    why_it_matters: str
    judge_adds: bool
    label_adds: bool
    label_note: str = ""


@dataclass
class WhyAgreementReport:
    """Confusion matrix between judge and label, with FILLER as the positive class.

    Cells (judge x label), positive = filler (i.e. ``adds_dimension == False``):
        ``tp`` : judge filler, label filler   (caught real filler)
        ``fp`` : judge filler, label adds      (wrongly flagged a good line)
        ``fn`` : judge adds,   label filler    (MISSED filler)
        ``tn`` : judge adds,   label adds       (agreed it earns its slot)
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    disagreements: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def agreement_rate(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 1.0

    @property
    def filler_precision(self) -> float | None:
        """Of lines the judge called filler, fraction the label agrees are filler."""
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else None

    @property
    def filler_recall(self) -> float | None:
        """Of lines the label calls filler, fraction the judge caught."""
        actual = self.tp + self.fn
        return self.tp / actual if actual else None


def score_agreement(cases: list[WhyCase]) -> WhyAgreementReport:
    """Compare judge ``adds_dimension`` verdicts against human labels (filler-positive)."""
    report = WhyAgreementReport()
    for c in cases:
        judge_filler = not c.judge_adds
        label_filler = not c.label_adds
        if judge_filler and label_filler:
            report.tp += 1
        elif judge_filler and not label_filler:
            report.fp += 1
        elif not judge_filler and label_filler:
            report.fn += 1
        else:
            report.tn += 1
        if c.judge_adds != c.label_adds:
            verdict = "judge=adds,label=filler" if c.judge_adds else "judge=filler,label=adds"
            report.disagreements.append(f"{verdict}: {c.why_it_matters[:80]}")
    return report


def load_golden_cases(golden_path: str | Path) -> list[WhyCase]:
    """Load ``why_judge_golden.json`` into WhyCase rows (judge + label per case)."""
    data = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    return [
        WhyCase(
            headline=c["headline"],
            summary=c["summary"],
            why_it_matters=c["why_it_matters"],
            judge_adds=bool(c["judge_adds_dimension"]),
            label_adds=bool(c["label_adds_dimension"]),
            label_note=c.get("label_note", ""),
        )
        for c in data.get("cases", [])
    ]
