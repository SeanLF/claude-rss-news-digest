"""Spec-contract tests for the WRITE stage prompt (.claude/agents/write.md).

Root-cause fix for the COHERENCE why_it_matters-only drop problem (2026-07-01):
COHERENCE validated perfectly on detection, but WRITE habitually seasons
why_it_matters with true-but-uncited background specifics ("6-3", "$60bn",
"last major city in Darfur") that no cited source supports, which was costing
up to 35% of stories on a real archived day even with graceful degradation in
merge.py. The durable fix is upstream: extend WRITE's existing citation
self-check (previously scoped to headline+summary only) to also cover
why_it_matters' concrete factual specifics.

These tests pin that the self-check text was actually extended, not just that
some words appear near each other -- a future edit narrowing the check back to
headline+summary would silently reopen the drop-rate problem COHERENCE now
catches structurally.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_PATH = REPO_ROOT / ".claude" / "agents" / "write.md"


def _body():
    return SPEC_PATH.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """The prompt text from `heading` up to the next bolded heading.

    Anchored on the real section boundary rather than a fixed character count so
    that adding a paragraph to a section cannot push a pinned rule out of the
    window and fail a test whose subject has not changed.
    """
    text = _body().lower()
    start = text.index(heading.lower())
    end = text.find("\n**", start)
    return text[start:] if end == -1 else text[start:end]


def test_citation_self_check_covers_why_it_matters():
    """The citation self-check section must explicitly extend to why_it_matters,
    not just headline/summary -- that's the whole point of the extension."""
    text = _body()
    idx = text.lower().index("citation self-check")
    section = text[idx : idx + 1200]
    assert "why_it_matters" in section.lower()


def test_citation_self_check_names_specific_types():
    """The self-check must still enumerate concrete specific types (numbers,
    dates, named prior events, quotes, office-holders) for why_it_matters, the
    same rigor already applied to headline/summary."""
    text = _body()
    idx = text.lower().index("citation self-check")
    section = text[idx : idx + 1200].lower()
    assert "number" in section
    assert "date" in section
    assert "quote" in section
    assert "prior event" in section or "named prior event" in section


def test_citation_self_check_permits_analysis_without_citation():
    """The fix must not require citing the analytical/mechanism content itself
    -- only concrete factual specifics inside why_it_matters. Otherwise this
    would neuter why_it_matters' by-design inferential purpose (see coherence.md's
    'Do NOT fail on' guard for the same distinction)."""
    text = _body()
    idx = text.lower().index("citation self-check")
    section = text[idx : idx + 1200].lower()
    assert "mechanism" in section or "analytical" in section or "consequence" in section


def test_why_it_matters_section_grounds_stakes_in_cited_articles():
    """Root-cause fix, second half: the why_it_matters writing section itself
    should tell the model to ground named stakes in the cited articles, not
    general/background knowledge -- prevention, not just a post-hoc check."""
    text = _body()
    idx = text.lower().index("why it matters (must_know")
    section = text[idx : idx + 1500].lower()
    assert "cited" in section
    assert "general knowledge" in section or "background knowledge" in section


# --- Repair-not-drop Step 0: WRITE-side prevention one-liners -----------------
# The COHERENCE reframe (feat/coherence-reframe-sonnet5) catches three error
# classes that are cheaper to PREVENT in WRITE than to repair downstream. Each
# rule pins one run-245 hard-positive that the current prompt does not explicitly
# block. Prevention costs zero recall, so these are the first repair-not-drop
# lever (HANDOVER.md Step 0).


def test_anti_overstatement_blocks_stronger_quantifier():
    """run-245 idx 3: headline said tariffs on 'most' Canadian goods when sources
    said 'some' / ~5% of exports. The existing 'qualifier MORE specific' bullet
    did not catch it (all six reframe runs missed it). WRITE must explicitly
    forbid using a quantifier stronger than the source's -- and it must apply to
    headlines, where this one shipped."""
    text = _body().lower()
    assert "quantifier" in text
    # The rule must be scoped to headlines, not summaries alone.
    idx = text.index("quantifier")
    window = text[max(0, idx - 400) : idx + 400]
    assert "headline" in window


def test_anti_overstatement_blocks_uncited_duration():
    """run-245 idx 4: summary said Burnham replaced Starmer 'after barely two
    years in office' -- a tenure length no cited source stated. WRITE must
    require a cited source for a duration / length-of-tenure specific."""
    text = _body().lower()
    assert "tenure" in text or "duration" in text
    idx = text.index("tenure" if "tenure" in text else "duration")
    window = text[max(0, idx - 300) : idx + 300]
    assert "cited" in window or "source" in window


# --- Null-delta continuations: WRITE must know what readers were already told ---
# Measured 2026-07-25: stories re-ship days later under a headline that restates
# the previous one. For the census cases that were thread continuations the delta
# EXISTED (7 facts for Burnham, 5 for the Spain wildfire) -- WRITE simply had no
# input carrying it, because threads are processed after assembly. Full rationale
# in test_write_recent_headlines.py.


def test_write_reads_recent_digest_headlines():
    """The file is useless if the prompt never opens it. Pinned because the read
    list is the whole interface -- prepare.py writing the file is a no-op
    otherwise, exactly the shape that made wire_agency a no-op through 841
    green tests."""
    text = _body()
    assert "recent_digest_headlines.txt" in text
    idx = text.index("recent_digest_headlines.txt")
    window = text[max(0, idx - 600) : idx + 200].lower()
    assert "read" in window  # must appear in the numbered read-these-files step


def test_continuation_headline_must_lead_with_what_changed():
    """Angle movement, not fact presence. Burnham had seven new facts available
    and still read as a repeat because the headline's angle was identical --
    audience research (IJoC) finds fatigue tracks the repeated angle rather than
    the volume of new facts. So the rule must demand the headline LEAD with the
    change, not merely permit re-coverage when facts exist."""
    assert "continuing stories" in _body().lower(), "no section instructing how to headline a continuation"
    section = _section("continuing stories")
    assert "lead with" in section
    assert "restate" in section or "reword" in section


def test_recent_headlines_never_gate_a_story():
    """This input must stay advisory. Novelty detection tops out near 75-80% on
    far richer input than we have, and its documented failure is scoring the
    RESOLUTION of an anticipated event as redundant with its anticipation -- we
    have a live instance (run 226, Le Pen conviction upheld, zero whats_new
    facts). WRITE must never drop or skip a story on this basis."""
    section = _section("continuing stories")
    assert "never" in section
    assert "drop" in section or "skip" in section or "omit" in section


def test_thin_source_summary_cap():
    """run-245 idx 15: the China-chips story's sole source was a bare 12-word
    headline with no full text, yet the summary invented two clauses. WRITE must
    cap a thin-source story (sole cited source is a bare headline, no full text)
    to a single sentence with no added specifics. Anchored on 'bare headline'
    (absent today) so the assertion fails until the real rule lands, and requires
    a sentence-cap co-located with it -- not the incidental 'One sentence' in the
    why_it_matters instruction."""
    text = _body().lower()
    assert "bare headline" in text
    idx = text.index("bare headline")
    window = text[max(0, idx - 350) : idx + 350]
    assert "one sentence" in window or "single sentence" in window or "one-sentence" in window


# --- One story per call: what write.md must NOT ask for --------------------
# WRITE is fanned out one call per story (write_fanout.py). The prompt is the
# per-story prompt; nothing rewrites it in flight any more, so an instruction a
# single-story call cannot satisfy has to be absent from the file itself.


def test_no_preheader_request():
    """A branch sees one story, so "the 2-3 biggest stories" is unsatisfiable on the
    same call that must not fabricate. preheader.md owns the field, and write.md has no
    reason to name it at all -- so the whole word is the pin. Lexical: a reinstatement
    that avoids the word would slip past, which is why the schema key is checked too."""
    body = _body().lower()
    assert "preheader" not in body
    assert '"preheader"' not in body


def test_no_batching_clauses():
    """Both clauses described under-citing / under-scrutinising "the ones you write
    last" -- a failure mode a single-story call cannot have."""
    text = _body().lower()
    assert "many stories at once" not in text
    assert "you write last" not in text


def test_the_self_checks_kept_their_scope_when_the_batching_clauses_went():
    """Negative control for the two deletions above: they removed one sentence each,
    not the rules those sentences sat in."""
    text = _body().lower()
    assert "list every specific in its headline and summary" in text
    assert "filler self-check" in text
