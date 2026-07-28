"""L1 eval floor: binary code-assertion graders for curated selections.

The cheapest, most foundational eval layer -- pure code assertions on the
curated `selections.json` output, no LLM-as-judge. Philosophy: binary
pass/fail only (never a 1-5 Likert), cheap enough to run on every change and
even in prod. Conceptually this is "the digest's schema validation extended"
with length caps, count ranges, and dedup checks the JSON schema can't express.

Wired into the live pipeline: merge._grade_assembled runs these on every
assembled selections.json, NON-FATALLY -- a failed check logs a warning and the
run continues. That is deliberate (they are an observability floor, not a gate),
but it does mean a check that raises loses the whole report, so every check must
tolerate any shape that survives schema validation.

The selections shape (see schema.SELECTIONS_SCHEMA) is::

    {
      "must_know":   [{headline, summary, why_it_matters, sources:[{article_id}], reporting_varies?}, ...],
      "should_know": [ ...same shape... ],
      "preheader":   "<= 150 chars"
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schema import validate_selections
from utils import ARTICLE_ID_GROUP

# Tiers that carry full article shape (headline + summary + why_it_matters + sources).
ARTICLE_TIERS = ("must_know", "should_know")

# The per-story WRITE fields that render verbatim to readers. reporting_varies is
# checked too (see _leak_checked_values) despite merge scrubbing it, because that
# scrubber is delimited-only -- "clean by construction" holds for exactly the form
# that is not the problem. not_covered_blurb is genuinely absent: merge DROPS the
# whole blurb rather than editing it, so nothing carrying a leak survives to here.
LEAK_CHECKED_ARTICLE_FIELDS = ("headline", "summary", "why_it_matters")


@dataclass(frozen=True)
class GraderLimits:
    """Configurable caps and ranges for the L1 graders.

    Defaults are set generously around current observed volume so they don't
    fail spuriously; the owner tightens targets later. Word caps use whitespace
    tokenisation (see ``_word_count``).
    """

    # Word caps (inclusive maxima).
    headline_max_words: int = 18
    summary_max_words: int = 80
    why_it_matters_max_words: int = 60

    # Preheader character cap (matches SELECTIONS_SCHEMA maxLength).
    preheader_max_chars: int = 150

    # Story-count ranges (inclusive [min, max]). Bounds track the live volume
    # policy (SELECT targets must_know 3-5 / hard-max 6, should_know 8-12 /
    # hard-max 14) with slack below target so the L1 floor catches bloat
    # regressions (e.g. should_know > 14) rather than spuriously failing.
    must_know_range: tuple[int, int] = (1, 6)
    should_know_range: tuple[int, int] = (3, 14)


@dataclass(frozen=True)
class Check:
    """A single binary L1 check result."""

    name: str
    passed: bool
    detail: str


@dataclass
class GradeReport:
    """The collection of binary checks plus a computed pass rate."""

    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail))

    @property
    def passed(self) -> bool:
        """True only if every check passed."""
        return all(c.passed for c in self.checks)

    @property
    def pass_rate(self) -> float:
        """Fraction of checks that passed (0.0-1.0); 1.0 when there are no checks."""
        if not self.checks:
            return 1.0
        return sum(1 for c in self.checks if c.passed) / len(self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def _word_count(text: str) -> int:
    return len(text.split())


def _normalize_title(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace -- for L1 dedup matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _iter_articles(selections: dict) -> list[tuple[str, dict]]:
    """Yield (tier, item) for every must_know/should_know story."""
    out: list[tuple[str, dict]] = []
    for tier in ARTICLE_TIERS:
        for item in selections.get(tier, []) or []:
            if isinstance(item, dict):
                out.append((tier, item))
    return out


def _all_headlines(selections: dict) -> list[str]:
    """Every headline across must_know and should_know."""
    heads = [item.get("headline", "") for _, item in _iter_articles(selections)]
    return [h for h in heads if isinstance(h, str)]


# --------------------------------------------------------------------------- #
# Individual checks. Each appends exactly one Check to the report.
# --------------------------------------------------------------------------- #


def _check_schema_valid(selections: dict, report: GradeReport) -> None:
    errors = validate_selections(selections)
    report.add(
        "schema_valid",
        passed=not errors,
        detail="ok" if not errors else f"{len(errors)} schema error(s): " + "; ".join(errors[:5]),
    )


def _check_required_fields_present(selections: dict, report: GradeReport) -> None:
    """must_know/should_know/preheader present and correctly typed (non-empty type)."""
    problems: list[str] = []
    for tier in ARTICLE_TIERS:
        val = selections.get(tier)
        if not isinstance(val, list):
            problems.append(f"{tier} missing or not a list")
    preheader = selections.get("preheader")
    if not isinstance(preheader, str) or not preheader.strip():
        problems.append("preheader missing or empty")
    report.add(
        "required_fields_present",
        passed=not problems,
        detail="ok" if not problems else "; ".join(problems),
    )


def _check_no_empty_strings(selections: dict, report: GradeReport) -> None:
    """No story has an empty/whitespace headline, summary, or why_it_matters."""
    offenders: list[str] = []
    for tier, item in _iter_articles(selections):
        for fld in ("headline", "summary", "why_it_matters"):
            val = item.get(fld, "")
            if not isinstance(val, str) or not val.strip():
                offenders.append(f"{tier}.{fld}")
    report.add(
        "no_empty_strings",
        passed=not offenders,
        detail="ok" if not offenders else f"{len(offenders)} empty field(s): " + ", ".join(offenders[:8]),
    )


def _check_word_cap(selections: dict, report: GradeReport, *, name: str, fld: str, cap: int) -> None:
    """Generic word-cap check across must_know/should_know stories for a field."""
    offenders: list[str] = []
    for tier, item in _iter_articles(selections):
        val = item.get(fld, "")
        if isinstance(val, str):
            wc = _word_count(val)
            if wc > cap:
                head = (item.get("headline") or "")[:50]
                offenders.append(f"{tier} ({wc}w > {cap}): {head!r}")
    report.add(
        name,
        passed=not offenders,
        detail=f"ok (cap {cap}w)" if not offenders else f"{len(offenders)} over cap: " + " | ".join(offenders[:5]),
    )


def _check_preheader_length(selections: dict, report: GradeReport, limits: GraderLimits) -> None:
    preheader = selections.get("preheader", "")
    n = len(preheader) if isinstance(preheader, str) else 0
    cap = limits.preheader_max_chars
    report.add(
        "preheader_length",
        passed=n <= cap,
        detail=f"{n} chars (cap {cap})",
    )


def _check_story_counts_in_range(selections: dict, report: GradeReport, limits: GraderLimits) -> None:
    counts = {
        "must_know": (len(selections.get("must_know") or []), limits.must_know_range),
        "should_know": (len(selections.get("should_know") or []), limits.should_know_range),
    }
    problems = [f"{tier}={n} not in [{lo},{hi}]" for tier, (n, (lo, hi)) in counts.items() if not (lo <= n <= hi)]
    summary = ", ".join(f"{tier}={n}" for tier, (n, _) in counts.items())
    report.add(
        "story_counts_in_range",
        passed=not problems,
        detail=summary if not problems else "; ".join(problems),
    )


def _check_sources_nonempty(selections: dict, report: GradeReport) -> None:
    """Every must_know/should_know story has >= 1 source."""
    offenders: list[str] = []
    for tier, item in _iter_articles(selections):
        sources = item.get("sources")
        if not isinstance(sources, list) or len(sources) == 0:
            head = (item.get("headline") or "")[:50]
            offenders.append(f"{tier}: {head!r}")
    report.add(
        "sources_nonempty",
        passed=not offenders,
        detail="ok" if not offenders else f"{len(offenders)} sourceless: " + " | ".join(offenders[:5]),
    )


def _check_dedup_vs_recent(selections: dict, report: GradeReport, recent_titles: set[str] | None) -> None:
    """No headline normalized-exact-matches a recently-shown title.

    Skipped (recorded as passing with a 'skipped' detail) when no recent titles
    are supplied -- this check only has signal when the caller provides history.
    """
    if recent_titles is None:
        report.add("dedup_vs_recent", passed=True, detail="skipped (no recent_titles provided)")
        return
    recent_norm = {_normalize_title(t) for t in recent_titles if isinstance(t, str) and t.strip()}
    dupes: list[str] = []
    for head in _all_headlines(selections):
        if _normalize_title(head) in recent_norm:
            dupes.append(head[:60])
    report.add(
        "dedup_vs_recent",
        passed=not dupes,
        detail=f"ok ({len(recent_norm)} recent titles)"
        if not dupes
        else f"{len(dupes)} dup(s): " + " | ".join(dupes[:5]),
    )


def _leaked_ids(text: str, cited: tuple[str, ...]) -> list[str]:
    """Internal article ids visible in one reader-facing string.

    Two detectors, because production has produced two shapes. Delimited groups
    ("(A316)", "[A221]") are caught by pattern. A BARE id is caught only when the
    story also cites it -- no pattern separates "A238" from "the A19 chip", but
    "cited by this very story" does, and it costs nothing in an observer.
    """
    spans = [m.span() for m in ARTICLE_ID_GROUP.finditer(text)]
    found = [text[a:b].strip() for a, b in spans]
    # Dedupe by POSITION, not by string. An id INSIDE a delimited match is the same leak; the
    # same id occurring again bare elsewhere is a second place someone has to look, and the
    # count is what tells them they have found them all.
    for i in cited:
        found += [i for m in re.finditer(rf"\b{re.escape(i)}\b", text) if not any(a <= m.start() < b for a, b in spans)]
    return found


def _cited_ids(item: dict) -> tuple[str, ...]:
    """The article ids a story cites, trimmed, blanks dropped.

    Container type is checked as strictly as the element type: a scalar ``sources`` ("A3" not
    ["A3"]) would iterate as characters, and a blank id makes ``re.escape("")`` compile to
    ``\b\b``, which matches at every word boundary and reports a clean story as leaking ''.
    Trimming mirrors ``threads._clean_fact``, which strips for the same reason.
    """
    sources = item.get("sources")
    if not isinstance(sources, list):
        return ()
    return tuple(
        t
        for s in sources
        if isinstance(s, dict) and isinstance(s.get("article_id"), str) and (t := s["article_id"].strip())
    )


def _leak_checked_values(item: dict):
    """(field, text) for every reader-facing string on a story, reporting_varies included."""
    for fld in LEAK_CHECKED_ARTICLE_FIELDS:
        if isinstance(value := item.get(fld), str):
            yield fld, value
    varies = item.get("reporting_varies")
    for entry in varies if isinstance(varies, list) else ():
        if isinstance(entry, dict):
            for key in ("source", "angle", "bias"):
                if isinstance(value := entry.get(key), str):
                    yield f"reporting_varies.{key}", value


def _check_no_internal_article_ids(selections: dict, report: GradeReport) -> None:
    """Flag any reader-facing WRITE field still carrying an internal article id.

    OBSERVE ONLY. This check never edits the text, and that asymmetry against
    merge's reporting_varies scrubber is the whole design.

    Run 247 (2026-07-28) shipped ``NYT (A316):`` into reporting_varies, a field
    write.md line 96 already told WRITE was "NOT article references". merge now
    strips that field. headline/summary/why_it_matters/preheader come from the
    same agent under the same prompt and render verbatim (render.render_article,
    render_email._story), and nothing checks them -- COHERENCE grades factuality,
    not leaks. So the exposure is real.

    But the remedy does not transfer. ``ARTICLE_ID_GROUP`` cannot distinguish an
    article id from a real "(A320)" Airbus or "(A7)" motorway -- ids run A1..A{n}
    with n in the hundreds, so the ranges genuinely overlap and no lexical rule
    separates them. That was an acceptable trade for a source NAME, where a
    misfire eats a parenthetical from an attribution label. It is not the same
    trade in a headline, which is also the story's slug, its dedup key, and the
    string COHERENCE verified. Across 195 published digests these four fields
    have leaked zero times, so there is no measured problem to justify putting a
    silent rewriter in front of every headline the product ships.

    Watching costs nothing and is strictly more informative: ``_grade_assembled``
    logs failures non-fatally, so the next real occurrence arrives in the run log
    with the field named and the match quoted -- which is exactly the evidence
    needed to decide whether stripping is warranted, and in which form.

    Two forms are checked. Delimited groups are caught by pattern. A BARE id is
    caught only when the story also cites it: nothing separates "A238" from "the
    A19 chip" lexically, but "cited by this very story" does, and it costs
    nothing in an observer.

    SCOPE, because the bare form is easy to over-claim: 2026-07-12 did ship a
    bare "...according to A238.", but through the THREAD path, which this check
    cannot see. The delta occupies the summary SLOT at render time
    (``render.py`` ``body = delta if delta else summary``); it is never written
    to ``item["summary"]``, thread context attaches inside ``write_digest``
    AFTER grading, and SELECTIONS_SCHEMA is ``additionalProperties: False`` with
    no ``thread`` key, so it cannot be present here. That leak is guarded in
    ``threads._clean_fact`` instead. The bare detector here is for WRITE doing
    the same thing in its own fields -- plausible under the same prompt, but so
    far UNOBSERVED. Do not cite 07-12 as evidence this check would have caught
    anything.

    reporting_varies is checked too, despite merge scrubbing it: merge's scrubber
    is delimited-only, so "clean by construction" holds for exactly the form that
    is not the problem.
    """
    offenders: list[str] = []
    story_offenders: list[str] = []
    all_cited: set[str] = set()
    for tier, item in _iter_articles(selections):
        raw_head = item.get("headline")
        head = raw_head[:50] if isinstance(raw_head, str) else repr(raw_head)[:50]
        cited = _cited_ids(item)
        all_cited.update(cited)
        for fld, value in _leak_checked_values(item):
            # Quote every match, not just a count: "was that an article id or an
            # aircraft?" is the only question the follow-up decision turns on.
            story_offenders += [f"{tier}.{fld} {m!r} in {head!r}" for m in _leaked_ids(value, cited)]
    # preheader FIRST: it is the most-seen string the pipeline emits, so appended last it would
    # be the first thing dropped by the cap below. It is written from the same stories, so the
    # self-citation premise holds against the union of every story's cited ids.
    preheader = selections.get("preheader")
    if isinstance(preheader, str):
        offenders += [f"preheader {m!r}" for m in _leaked_ids(preheader, tuple(sorted(all_cited)))]
    offenders += story_offenders
    report.add(
        "no_internal_article_ids",
        passed=not offenders,
        detail="ok" if not offenders else f"{len(offenders)} leak(s): " + " | ".join(offenders[:5]),
    )


def grade_selections(
    selections: dict,
    *,
    recent_titles: set[str] | None = None,
    limits: GraderLimits | None = None,
) -> GradeReport:
    """Run all L1 binary checks against a parsed selections payload.

    Args:
        selections: parsed selections.json dict.
        recent_titles: recently-shown RSS/headline strings for dedup; when None
            the dedup check is recorded as skipped (passing).
        limits: configurable caps/ranges; defaults to ``GraderLimits()``.

    Returns:
        A GradeReport with one Check per L1 assertion.
    """
    limits = limits or GraderLimits()
    report = GradeReport()

    _check_schema_valid(selections, report)
    _check_required_fields_present(selections, report)
    _check_no_empty_strings(selections, report)
    _check_word_cap(selections, report, name="headline_length", fld="headline", cap=limits.headline_max_words)
    _check_word_cap(selections, report, name="summary_length", fld="summary", cap=limits.summary_max_words)
    _check_word_cap(
        selections, report, name="why_it_matters_length", fld="why_it_matters", cap=limits.why_it_matters_max_words
    )
    _check_preheader_length(selections, report, limits)
    _check_story_counts_in_range(selections, report, limits)
    _check_sources_nonempty(selections, report)
    _check_dedup_vs_recent(selections, report, recent_titles)
    _check_no_internal_article_ids(selections, report)

    return report
