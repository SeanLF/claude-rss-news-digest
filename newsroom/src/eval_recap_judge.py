"""L2 eval: the RECAP theme-coverage judge (Haiku-vs-Sonnet quality metric).

RECAP summarises a week of shown RSS titles into 2-3 thematic sentences that give
SELECT context to de-prioritise stale stories. Its load-bearing failure mode is
OMISSION -- dropping a theme that was actually prominent, so SELECT loses the
signal -- and, secondarily, FABRICATION (asserting a theme no title supports).

`grade_recap` (L1) only checks shape (non-empty/length/sentences/no-bullets); it
cannot see whether the *content* is faithful. This judge is that metric: given a
recap and its source titles, it returns the prominent themes the recap MISSED and
any themes it FABRICATED. The judge runs on a strong model (Sonnet) precisely
because we use it to decide whether a *weaker* model (Haiku) holds up.

Two surfaces, mirroring eval_why_judge:
  * RECAP_JUDGE_PROMPT + judge_recap -- the live judge (one structured call via
    the Agent SDK wrapper, sharing the pipeline's auth; no ANTHROPIC_API_KEY).
  * score_agreement + RecapAgreementReport -- pure-code offline scoring of judge
    verdicts against independent human labels (no model calls; tests / the gate).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# The judge prompt (v1). Validation numbers live in recap_judge_golden.json _meta.
# --------------------------------------------------------------------------- #

RECAP_JUDGE_PROMPT = """You are auditing a weekly news RECAP against the source RSS titles it summarised.

The recap should capture the MAJOR themes of the week in 2-3 sentences -- the topics that appear across MANY distinct titles or recur over multiple days. It is NOT meant to mention every story; minor or one-off stories are fine to omit.

You are given the recap and the list of source titles. Decide two things:

1. missed_themes: MAJOR themes clearly prominent in the titles (covered by several distinct titles, or recurring) that are ABSENT from the recap. Do NOT list minor or single-title topics -- only genuinely prominent themes the recap should have carried.

2. fabricated_themes: themes or specific claims the recap asserts that NO title supports (the recap invented them).

Rules:
- A theme the recap covers in DIFFERENT wording is COVERED -- do not flag it as missed.
- A topic in only one or two titles is minor -- not a missed MAJOR theme.
- Only flag a fabrication when no title supports it; vaguer thematic phrasing of real titles is fine.
- Judge content, not style.

Respond with ONLY a JSON object, no prose around it:
{"missed_themes": ["<prominent theme absent from the recap>"], "fabricated_themes": ["<unsupported claim in the recap>"]}
Empty lists mean the recap is faithful."""

JUDGE_MODEL = "claude-sonnet-4-6"

# Titles passed to the judge are deduped and capped: enough to represent the week
# without flooding the context (windows can carry ~1000 near-duplicate-heavy rows).
DEFAULT_TITLE_CAP = 300


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def prepare_titles(titles: list[str], cap: int = DEFAULT_TITLE_CAP) -> list[str]:
    """Drop exact (normalized) duplicate titles, preserve order, cap the count."""
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        key = _norm(t)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= cap:
            break
    return out


def parse_recap_verdict(text: str) -> tuple[list[str], list[str]]:
    """Extract (missed_themes, fabricated_themes) from the judge's text output.

    Scans for JSON objects carrying ``missed_themes`` and returns the LAST one.
    Unlike the why-judge -- whose schema example is invalid JSON (``true|false``)
    and self-skips -- this judge's schema example is valid JSON, so a model that
    echoes the schema before answering would yield two parseable objects; the real
    verdict is always the last. Raises ValueError if none is found -- a silently
    defaulted verdict would corrupt the metric.
    """
    decoder = json.JSONDecoder()
    found: tuple[list[str], list[str]] | None = None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "missed_themes" in obj:
            missed = [str(x) for x in obj.get("missed_themes", []) if str(x).strip()]
            fabricated = [str(x) for x in obj.get("fabricated_themes", []) if str(x).strip()]
            found = (missed, fabricated)
    if found is None:
        raise ValueError(f"recap-judge returned no JSON object with missed_themes: {text[:200]!r}")
    return found


def _build_user_prompt(recap_text: str, titles: list[str]) -> str:
    listed = "\n".join(f"- {t}" for t in titles)
    return f"RECAP:\n{recap_text}\n\nSOURCE TITLES ({len(titles)}):\n{listed}"


def judge_recap(
    recap_text: str,
    titles: list[str],
    *,
    model: str = JUDGE_MODEL,
    timeout: int = 90,
    title_cap: int = DEFAULT_TITLE_CAP,
) -> tuple[list[str], list[str]]:
    """Live judge: returns (missed_themes, fabricated_themes) for one recap.

    Routed through claude_cli.run_sync so it shares the pipeline's OAuth/credit
    auth rather than needing ANTHROPIC_API_KEY.
    """
    import claude_cli  # local import keeps the pure-code scoring path dependency-free

    prepared = prepare_titles(titles, cap=title_cap)
    text = claude_cli.run_sync(
        _build_user_prompt(recap_text, prepared),
        model=model,
        system_prompt=RECAP_JUDGE_PROMPT,
        max_turns=1,
        timeout=timeout,
    )
    return parse_recap_verdict(text)


# --------------------------------------------------------------------------- #
# Offline scoring (pure code, no model calls).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RecapCase:
    """One recap joined to the judge's verdict and the independent human label.

    ``clean`` = faithful (no missed major theme, no fabrication). The positive
    class we care about detecting is the inverse -- a DEFECTIVE recap. A defective
    recap the judge calls clean is a missed defect (false negative).
    """

    window_id: str
    model: str
    judge_clean: bool
    label_clean: bool
    judge_missed: list[str] = field(default_factory=list)
    judge_fabricated: list[str] = field(default_factory=list)
    label_note: str = ""


@dataclass
class RecapAgreementReport:
    """Confusion matrix between judge and label, DEFECTIVE as the positive class.

    ``tp`` : judge defective, label defective   (caught a real defect)
    ``fp`` : judge defective, label clean        (over-flagged a fine recap)
    ``fn`` : judge clean,     label defective     (MISSED a defect)
    ``tn`` : judge clean,     label clean          (agreed it is faithful)
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
    def defect_precision(self) -> float | None:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else None

    @property
    def defect_recall(self) -> float | None:
        actual = self.tp + self.fn
        return self.tp / actual if actual else None


def score_agreement(cases: list[RecapCase]) -> RecapAgreementReport:
    """Compare judge faithfulness verdicts against human labels (defect-positive)."""
    report = RecapAgreementReport()
    for c in cases:
        judge_defective = not c.judge_clean
        label_defective = not c.label_clean
        if judge_defective and label_defective:
            report.tp += 1
        elif judge_defective and not label_defective:
            report.fp += 1
        elif not judge_defective and label_defective:
            report.fn += 1
        else:
            report.tn += 1
        if c.judge_clean != c.label_clean:
            verdict = "judge=clean,label=defective" if c.judge_clean else "judge=defective,label=clean"
            report.disagreements.append(f"{c.window_id}/{c.model}: {verdict}")
    return report


def load_theme_golden(golden_path: str | Path) -> list[dict]:
    """Load the theme-level precision golden (each flagged theme + human label_real)."""
    data = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    return list(data.get("cases", []))


def theme_precision(cases: list[dict]) -> float:
    """Fraction of judge-flagged themes a human confirms are real prominent omissions.

    The decision-relevant validation metric for this judge: binary clean/defective
    is degenerate (every recap drops something), so what matters is whether a
    flagged miss is REAL. High precision => the paired missed-theme comparison is
    comparing real omissions.
    """
    if not cases:
        return 1.0
    return sum(1 for c in cases if c.get("label_real")) / len(cases)


def load_golden_cases(golden_path: str | Path) -> list[RecapCase]:
    """Load recap_judge_golden.json into RecapCase rows (judge verdict + human label)."""
    data = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    return [
        RecapCase(
            window_id=c["window_id"],
            model=c.get("model", ""),
            judge_clean=bool(c["judge_clean"]),
            label_clean=bool(c["label_clean"]),
            judge_missed=list(c.get("judge_missed", [])),
            judge_fabricated=list(c.get("judge_fabricated", [])),
            label_note=c.get("label_note", ""),
        )
        for c in data.get("cases", [])
    ]
