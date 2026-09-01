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

from schema import PREHEADER_MAX_CHARS, validate_selections
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

    # Word caps (inclusive maxima), set at the p99 of shipped output over runs 241-280
    # (40 runs, 635 stories). At the previous values these fired on 38.4% and 22.2% of
    # healthy output -- against this class's own "don't fail spuriously".
    headline_max_words: int = 20
    summary_max_words: int = 120
    why_it_matters_max_words: int = 80

    # DERIVED, not restated: the comment here used to claim it matched SELECTIONS_SCHEMA
    # while the schema said 157 and this said 150.
    preheader_max_chars: int = PREHEADER_MAX_CHARS

    # Share of why_it_matters' content tokens that also occur in its own summary
    # (inclusive maximum). Bounded from BELOW by simulated repair output, not by the
    # shipped p99 -- see _check_why_it_matters_restates_summary.
    why_restatement_max_overlap: float = 0.70

    # write.md tells WRITE why_it_matters is "One sentence". The cap IS the spec, not a
    # percentile: the word caps sit at a percentile because nothing specifies them.
    why_it_matters_max_sentences: int = 1

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


# Function words carry no topic, so leaving them in puts a floor under every overlap
# score. WHICH words are in the list is not load-bearing -- see
# test_the_cap_does_not_depend_on_the_stop_list.
_STOP_WORD_TEXT = """
a an the and or but if then than that this these those of in on at to for from by with
as is are was were be been being it its he she they them his her their we you i our your
has have had do does did not no nor so such can could will would shall should may might must
about into over under after before between during against through above below up down out off
own same too very just also more most other some any each both which who whom whose what when
where why how there here now new one two first second us said says say while because
"""
_STOP_WORDS = frozenset(_STOP_WORD_TEXT.split())

# Tokens of 1-2 characters are initialisms and units that recur regardless of topic.
_MIN_CONTENT_TOKEN_CHARS = 3


def _content_tokens(text: str) -> list[str]:
    """Lowercased alphanumeric tokens with stop words and very short tokens dropped."""
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP_WORDS and len(t) >= _MIN_CONTENT_TOKEN_CHARS
    ]


def _restatement_overlap(why: str, summary: str) -> float:
    """Share of why_it_matters' content tokens that also occur in its own summary.

    Positional, not set-based: a flattening that leans on one entity repeatedly scores
    higher, which is the direction the failure moves. The set-based form separated the same
    populations just as well when measured, so this is a tie broken on the failure mode
    rather than on evidence.

    Returns 0.0 for an empty numerator -- see the empty-field test for why that is a
    pass and not a 1.0.
    """
    tokens = _content_tokens(why)
    if not tokens:
        return 0.0
    in_summary = set(_content_tokens(summary))
    return sum(1 for t in tokens if t in in_summary) / len(tokens)


# Abbreviation-aware sentence splitting: a naive split on a terminator plus whitespace
# over-counts real shipped lines and never under-counts them, and every one of its spurious
# splits is "U.S." -- see test_abbreviations_are_not_sentence_ends. Counts in the commit.
#
# TWO letter-dot pairs, not one, is a TRADE like the quote rule below. It keeps the real
# sentence end in "The grade was an A. The school objected" -- under-counting is the
# direction that goes unnoticed -- and it costs a false positive on a middle initial
# ("Robert F. Kennedy Jr."). Both sides pinned; see the over-count test.
_INITIALISM = r"(?:\b(?:[A-Z]\.){2,})"
_LOWER_ABBREVIATION = r"(?:\b(?:a\.m|p\.m|e\.g|i\.e)\.)"
# Also a trade: (?i:) buys "Dr."/"Sept."/"No. 10" and costs the under-count on ordinary
# words it collides with ("was no. The vote"). Both sides pinned in the splitter tests.
_TITLE_ABBREVIATION = (
    r"(?i:\b(?:Mr|Mrs|Ms|Dr|Prof|Gen|Sen|Rep|St|Lt|Col|Sgt|Gov|Pres|Rev|Hon"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
    r"|No|Vol|Art|Inc|Ltd|Co|Corp|vs|etc|approx|est|Fig)\.)"
)
_ABBREVIATION = re.compile("|".join((_INITIALISM, _LOWER_ABBREVIATION, _TITLE_ABBREVIATION)))

# A terminator ends a sentence only before an opener (uppercase, digit, quote, bracket) or
# end of string, so a decimal or an ellipsis mid-clause does not split. Allowing a closing
# quote between the terminator and the space is a TRADE, not a free fix: it costs a false
# positive on a terminator INSIDE a quotation. Both sides are pinned as tests.
_SENTENCE_BOUNDARY = re.compile(r"[.?!]+[\"'\u201d\u2019)\]]*(?:\s+(?=[\"'\u201c\u2018(\[A-Z0-9])|\s*$)")


def _count_sentences(text: str) -> int:
    """Sentences in one reader-facing string, abbreviations masked first."""
    masked = _ABBREVIATION.sub(lambda m: m.group(0).replace(".", "\x00"), text.strip())
    return len([part for part in _SENTENCE_BOUNDARY.split(masked) if part.strip()])


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


def _check_why_it_matters_restates_summary(selections: dict, report: GradeReport, limits: GraderLimits) -> None:
    """Flag a why_it_matters rebuilt out of its own summary's vocabulary.

    READ THE SCOPE BEFORE TRUSTING THIS. It is a NEAR-COPY tripwire, not a restatement
    detector, and docs/lessons/best-practices/a-lexical-detector-is-anti-correlated-with-
    a-rewording-defect.md (severity high, applies_when "Detecting duplicate, restated or
    'no new information' model output") already measured the general form at ~10% recall:
    "Only the near-verbatim case is detectable."

    That holds here, on write.md's own worked pair: write.md's "Filler self-check" gives a
    filler example and the line that should replace it, and the filler scores LOWER than
    the replacement against real summaries. The metric is inverted on the one degeneracy
    the prompt documents, so this catches a repair that re-emits the summary's words and
    does not catch filler. No threshold changes that. Do not use it to size the problem --
    a detector's recall bounds every prevalence estimate made with it, and its recall
    against fluent restatement is unmeasured because the population barely exists.

    IT ALSO CANNOT SEE A THREAD DAY. It compares why_it_matters against item["summary"],
    but render.py uses `body = delta if delta else summary`, and attach_thread_context runs
    inside write_digest AFTER grading -- so on a continuation the reader's body is a string
    this check never receives. A why_it_matters that restates the DELTA is invisible here.
    Same mechanism _check_no_internal_article_ids documents for its own scope.

    The sign is undefined at the short end, in both directions. A one-content-token
    why_it_matters can only score 0.0 or 1.0, so gutting to "It matters." usually passes and
    fires only when the summary happens to contain "matters" -- reported under a check name
    that is then the wrong name for the fault. Shortening to one summary-derived clause
    scores 1.000 and fires. Closing that needs a minimum-content check of its own, not a
    floor smuggled in here.
    """
    cap = limits.why_restatement_max_overlap
    offenders: list[str] = []
    for tier, item in _iter_articles(selections):
        why, summary = item.get("why_it_matters"), item.get("summary")
        if not isinstance(why, str) or not isinstance(summary, str):
            continue
        overlap = _restatement_overlap(why, summary)
        if overlap > cap:
            head = (item.get("headline") or "")[:50]
            offenders.append(f"{tier} ({overlap:.3f} > {cap:.3f}): {head!r}")
    report.add(
        "why_it_matters_restates_summary",
        passed=not offenders,
        detail=f"ok (cap {cap:.3f} overlap)"
        if not offenders
        else f"{len(offenders)} restating summary: " + " | ".join(offenders[:5]),
    )


def _check_why_it_matters_sentence_count(selections: dict, report: GradeReport, limits: GraderLimits) -> None:
    """Flag a why_it_matters longer than the one sentence write.md specifies.

    OBSERVE ONLY in the sense _check_no_internal_article_ids uses -- it never edits the
    text. It IS gated: the baseline records it and bin/eval-regression fails on a
    PASS -> FAIL flip.

    write.md tells WRITE the field is "One sentence" and nothing graded it,
    so the constraint lived only in prose. The cap is pinned to that wording by a test,
    because a cap and a spec that drift apart are worse than neither. The rule is not
    decorative: the field is the story's analytic payload, and a second sentence is where
    a restatement of the summary, or a second unsupported claim for COHERENCE to flag,
    gets appended.

    The cap is the spec, not a percentile -- the word caps in GraderLimits sit at the p99
    of shipped output because nothing specifies them. Shipped output almost always meets
    the spec already, so most of this check's value is forward drift rather than history:
    per-run counts are in the detail string, and most of the runs it flags today already
    fail another check. Numbers in the commit.
    """
    cap = limits.why_it_matters_max_sentences
    offenders: list[str] = []
    for tier, item in _iter_articles(selections):
        why = item.get("why_it_matters")
        if not isinstance(why, str) or not why.strip():
            continue
        n = _count_sentences(why)
        if n > cap:
            head = (item.get("headline") or "")[:50]
            offenders.append(f"{tier} ({n} sentences > {cap}): {head!r}")
    report.add(
        "why_it_matters_sentence_count",
        passed=not offenders,
        detail=f"ok (cap {cap})" if not offenders else f"{len(offenders)} over cap: " + " | ".join(offenders[:5]),
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
    _check_why_it_matters_restates_summary(selections, report, limits)
    _check_why_it_matters_sentence_count(selections, report, limits)

    return report
