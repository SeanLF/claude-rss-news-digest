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
