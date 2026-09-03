"""Spec-contract tests for the REPAIR stage prompt (.claude/agents/repair.md).

Every test in ``test_repair.py`` feeds a SYNTHETIC ``repaired_fields.json``, so nothing
there can see the prompt drift out of step with ``merge._REPAIRABLE_FIELDS``. These pin
the prompt against the code, as ``test_coherence_spec.py`` and ``test_write_spec.py`` do.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import merge
import orchestrate

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_PATH = REPO_ROOT / ".claude" / "agents" / "repair.md"


def _spec():
    return orchestrate.parse_agent_spec(SPEC_PATH)


def test_failed_fields_line_names_every_repairable_field():
    """The line that DEFINES `failed_fields` must name exactly the fields the code sends.

    Scoped to that line on purpose: the prompt mentions every field incidentally (as
    unflagged context the repairer must NOT edit), so a whole-body substring check would
    pass on a prompt whose enumeration is narrower than the code's set.
    """
    lines = [ln for ln in _spec().body.splitlines() if "`failed_fields`" in ln and "repair" in ln.lower()]
    assert lines, "repair.md has no line defining `failed_fields`"
    enumeration = " ".join(lines)
    missing = [f for f in sorted(merge._REPAIRABLE_FIELDS) if f not in enumeration]
    assert not missing, (
        f"{missing} are in merge._REPAIRABLE_FIELDS but the `failed_fields` line does not "
        f"name them, so the repairer is told they cannot appear: {enumeration!r}"
    )


def test_prompt_does_not_promise_a_narrower_field_set():
    """The prompt must not tell the model a repairable field can 'never' appear -- a
    closed-world enumeration goes stale the moment the code's set grows."""
    body = _spec().body
    narrowing = [
        line
        for line in body.splitlines()
        if "never anything else" in line.lower() and not all(f in line for f in merge._REPAIRABLE_FIELDS)
    ]
    assert not narrowing, f"repair.md closes the field list without naming every repairable field: {narrowing}"


def test_prompt_requires_exactly_the_flagged_fields():
    """`apply_repairs` guards `set(present) != flagged` -- no more, no less. The prompt has
    to ask for exactly that, or a well-behaved model still gets rejected."""
    body = _spec().body.lower()
    assert "failed_fields" in body
    assert "no more and no less" in body or "exactly the field" in body


def test_prompt_requires_one_object_per_story():
    """Multi-field repairs must arrive in ONE object. `_merge_repaired_by_article_ids`
    tolerates a split, but tolerance is the backstop -- the contract is one object, and a
    union merge cannot express the model WITHDRAWING a field it earlier proposed."""
    body = _spec().body.lower()
    assert "one object per story" in body


def test_model_is_pinned_and_recognised():
    """A drifted alias silently changes cost and behaviour; usage._PINNED_MODEL_IDS is the
    project's list of models it expects to see in run_usage."""
    import usage

    assert _spec().model.startswith(usage._PINNED_MODEL_IDS)


def test_body_carries_the_current_date_token():
    """The repairer reasons about world state (who holds an office), so it needs the run
    date rather than a stale training prior -- the same fix as WRITE's."""
    assert orchestrate._CURRENT_DATE_TOKEN in _spec().body


def test_prompt_forbids_internal_ids_in_reader_facing_text():
    """`_INTERNAL_ID_PATTERNS` rejects a patch containing an article id, so the prompt must
    say so -- otherwise the guard silently drops stories the repairer thought it fixed."""
    body = _spec().body.lower()
    assert "article_id" in body or "article id" in body
    assert "never output" in body or "must never" in body


def test_a_repaired_why_it_matters_keeps_the_one_sentence_cap():
    """Run 285: the Serbia repair was correct against its source and came back at 86 words in
    two sentences, over both caps write.md sets; the repair guard checks empty and id-leak
    only, so the prompt is where the cap has to live."""
    body = _spec().body
    start = body.index("**Repairing `why_it_matters`.**")
    end = body.find("\n**", start + 1)
    section = body[start : end if end != -1 else None].lower()
    assert "one sentence" in section
