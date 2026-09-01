"""Spec-contract tests for the COHERENCE stage prompt (.claude/agents/coherence.md).

COHERENCE fact-checks each story against its cited sources. It was extended to
check summary + why_it_matters, not just the headline -- a shipped bug ("puts the
Biden administration in a bind", 2026-07-01 prod digest) was in a summary, which
the headline-only net could not structurally catch.

The stage stays on Sonnet: a paired snapshot validation (2026-07-01,
scratch/coherence_val/) showed Haiku missing 3 of 4 planted summary fabrications
and losing a borderline-headline head-to-head 0/4 vs Sonnet's 4/4 -- the
detection register is NOT Haiku-mechanical, it needs careful per-specific
verification. The same validation showed two prompt loopholes get exploited:
a "background facts" carve-out (a fabricated statistic was rationalized as
well-known background) and a pass-on-doubt tiebreaker. Both are now closed:
statistics are never background, and uncertainty about SUPPORT is a fail
(only genuine analysis-vs-fact ambiguity passes).

These tests pin that contract (model, which fields are checked, the strictness
direction, the narrow anti-over-drop guard, and the unchanged output schema
shape) so a future edit can't silently narrow the check back to headline-only,
change the output shape Python parses, swap the model without re-validating, or
reintroduce the over-drop failure mode (incident history: an earlier COHERENCE
version over-dropped valid headlines by policing paraphrase).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_PATH = REPO_ROOT / ".claude" / "agents" / "coherence.md"


def _spec():
    return orchestrate.parse_agent_spec(SPEC_PATH)


def test_model_is_sonnet():
    """Detection quality is model-bound: Haiku missed planted fabrications 2026-07-01
    (scratch/coherence_val/), and a harness-faithful eval on 2026-07-21 showed Sonnet 5
    catches hallucinations Sonnet 4.6 missed (0/6 -> best 4/6, 0 false drops; see
    docs/2026-07-21-coherence-reframe-design.md). Swapping this model requires re-running
    that validation via `make eval-coherence`."""
    assert _spec().model == "claude-sonnet-5"


def test_description_mentions_summary_and_why_it_matters():
    text = SPEC_PATH.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "summary" in frontmatter.lower()
    assert "why_it_matters" in frontmatter.lower()


def test_body_checks_summary_and_why_it_matters():
    """The check must cover summary and why_it_matters, not just the headline --
    that's the whole point of the extension (a real bug shipped in a summary)."""
    body = _spec().body.lower()
    assert "summary" in body
    assert "why_it_matters" in body


def test_body_has_do_not_fail_guard():
    """THE DON'T-OVER-DROP RULE: framing/tone/paraphrase and why_it_matters'
    inferential content must not fail -- only fabrication/contradiction/stale
    world-state can."""
    body = _spec().body.lower()
    assert "do not fail on" in body
    assert "framing" in body
    assert "why_it_matters" in body


def test_body_is_strict_on_unsupported_specifics():
    """Strictness direction: an unverifiable specific FAILS (pass-on-doubt was
    validated as a miss-regression and removed)."""
    body = _spec().body.lower()
    assert "be strict" in body
    assert "fail, not a pass" in body or "is a fail" in body


def test_statistics_are_never_background_facts():
    """Validation loophole: a fabricated statistic was passed as 'well-known
    background'. The carve-out must exclude statistics explicitly."""
    body = _spec().body.lower()
    assert "never" in body and "background" in body
    assert "statistic" in body


def test_output_schema_shape_unchanged():
    """Python (merge.assemble_selections, orchestrate.validate_coherence) parses
    {"results": [{"headline", "article_ids", "pass", "reason"}, ...]} -- one result
    per STORY. The prompt must not switch to one-result-per-field."""
    body = _spec().body
    assert '"results"' in body
    assert '"headline"' in body
    assert '"article_ids"' in body
    assert '"pass"' in body
    assert '"reason"' in body
    assert "one result per" in body.lower()


def test_reason_prefixed_with_failing_field():
    """reason is free text (Python doesn't parse it), but the prompt should tell the
    model to prefix it with the failing field so a human/judge can tell what broke."""
    body = _spec().body
    assert "prefix" in body.lower()


def test_output_schema_mentions_failed_fields():
    """Graceful degradation: merge.py needs a machine-parseable failed_fields
    list (subset of headline/summary/why_it_matters) on pass:false results so it
    can blank a why_it_matters-only failure instead of dropping the whole story
    -- COHERENCE validated perfectly on detection but a headline-drop-on-any-fail
    policy was costing up to 35% of stories on a real archived day (WRITE
    habitually seasons why_it_matters with true-but-uncited background
    specifics). This test pins that the prompt actually asks for the field."""
    body = _spec().body
    assert "failed_fields" in body


def test_failed_fields_schema_example_present():
    """The schema example block itself must show failed_fields, not just prose
    describing it -- prompt examples are what models actually pattern-match."""
    body = _spec().body
    assert '"failed_fields"' in body


def test_binding_probe_is_not_scoped_to_relations_or_the_headline():
    """Measured 2026-08-31 on the run-245 fixture: widening probe 2 to SCOPE/TIME-WINDOW/
    EVENT-PARTICIPANT and lifting probe 3 off the headline took idx 0 from 0/4 to 4/5 and idx 4
    from 2/4 to 4/5, false-drops 0/35. Narrowing either back is the regression."""
    body = _spec().body
    assert "SCOPE and TIME-WINDOW" in body
    assert "EVENT-PARTICIPANT" in body
    assert "In EVERY field, verify each named entity" in body


def test_probe_one_still_asks_for_the_single_least_supported_specific():
    """The 'list EVERY specific' rewrite (audit hunk F2) was MEASURED AND REJECTED: it moved
    nothing its own target cared about (idx 3, 0/6) and both gains it appeared to produce were
    reproduced by F3 alone. Kept out deliberately -- re-adding it lengthens the most expensive
    prompt in the pipeline for no measured effect."""
    assert "Find the single least-supported specific" in _spec().body


def test_no_effort_override():
    """`effort: xhigh` was MEASURED AND REJECTED on 2026-08-31: identical to default effort on
    recall (4.60), idx 0 (4/5), idx 3 (0/5) and false-drops (0/35), for ~33% more wall clock.
    run_usage.effort is NULL by choice, not oversight."""
    assert _spec().effort is None


def test_body_carries_the_current_date_token():
    """coherence.md auto-fails a STALE WORLD-STATE assertion and tells the model to check
    "the cited articles and today's date" -- so it has to be given the date."""
    assert orchestrate._CURRENT_DATE_TOKEN in _spec().body
