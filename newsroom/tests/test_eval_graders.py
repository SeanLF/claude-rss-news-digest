"""Tests for eval_graders.py (L1 binary code-assertion graders).

Each test proves a single check fires correctly: a known-good fixture passes
everything, and targeted broken fixtures make exactly the relevant check fail.
No network, no DB -- small inline fixtures only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_graders import GraderLimits, grade_selections


def _article(headline="A short, clear headline about something", article_id="A1"):
    return {
        "headline": headline,
        "summary": "A concise summary of the story in a single readable sentence.",
        "why_it_matters": "Why this matters, kept brief and to the point.",
        "sources": [{"article_id": article_id}],
    }


def _good_selections():
    """All checks pass: counts in range, lengths under caps."""
    return {
        "must_know": [_article(f"Must-know story number {i}") for i in range(2)],
        "should_know": [_article(f"Should-know story number {i}") for i in range(4)],
        "preheader": "A short preheader well under the 150 character cap.",
    }


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def _names_failed(report):
    return {c.name for c in report.failures}


class TestKnownGood:
    def test_all_checks_pass(self):
        report = grade_selections(_good_selections())
        assert report.passed, f"unexpected failures: {_names_failed(report)}"
        assert report.pass_rate == 1.0

    def test_dedup_skipped_when_no_recent(self):
        report = grade_selections(_good_selections())
        assert _check(report, "dedup_vs_recent").passed
        assert "skipped" in _check(report, "dedup_vs_recent").detail

    def test_report_has_all_expected_checks(self):
        report = grade_selections(_good_selections())
        names = {c.name for c in report.checks}
        assert names == {
            "schema_valid",
            "required_fields_present",
            "no_empty_strings",
            "headline_length",
            "summary_length",
            "why_it_matters_length",
            "preheader_length",
            "story_counts_in_range",
            "sources_nonempty",
            "dedup_vs_recent",
            "no_internal_article_ids",
        }


class TestSchemaValid:
    def test_extra_property_fails_schema(self):
        sel = _good_selections()
        sel["must_know"][0]["bogus_field"] = "x"
        report = grade_selections(sel)
        assert not _check(report, "schema_valid").passed


class TestNoEmptyStrings:
    def test_empty_why_it_matters_fails(self):
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = "   "
        report = grade_selections(sel)
        assert not _check(report, "no_empty_strings").passed
        assert "why_it_matters" in _check(report, "no_empty_strings").detail

    def test_empty_headline_fails(self):
        sel = _good_selections()
        sel["should_know"][0]["headline"] = ""
        report = grade_selections(sel)
        assert not _check(report, "no_empty_strings").passed


class TestLengthCaps:
    def test_overlong_summary_fails_only_summary(self):
        sel = _good_selections()
        sel["must_know"][0]["summary"] = "word " * 200
        report = grade_selections(sel)
        assert not _check(report, "summary_length").passed
        assert _check(report, "headline_length").passed
        assert _check(report, "why_it_matters_length").passed

    def test_overlong_headline_fails(self):
        sel = _good_selections()
        sel["must_know"][0]["headline"] = "word " * 30
        report = grade_selections(sel)
        assert not _check(report, "headline_length").passed

    def test_overlong_why_it_matters_fails(self):
        sel = _good_selections()
        sel["should_know"][0]["why_it_matters"] = "word " * 100
        report = grade_selections(sel)
        assert not _check(report, "why_it_matters_length").passed

    def test_configurable_caps_respected(self):
        sel = _good_selections()
        # Default cap is 80; tighten to a value the good summary now exceeds.
        tight = GraderLimits(summary_max_words=3)
        report = grade_selections(sel, limits=tight)
        assert not _check(report, "summary_length").passed
        # Loosen back: same content passes under a generous cap.
        loose = GraderLimits(summary_max_words=1000)
        assert _check(grade_selections(sel, limits=loose), "summary_length").passed


class TestPreheaderLength:
    def test_one_char_over_the_schema_cap_fails(self):
        """Derived from the cap, not restated: this test hardcoded 151 and so silently stopped
        exercising the boundary the moment the cap moved."""
        sel = _good_selections()
        sel["preheader"] = "x" * (GraderLimits().preheader_max_chars + 1)
        report = grade_selections(sel)
        assert not _check(report, "preheader_length").passed

    def test_exactly_at_the_cap_passes(self):
        sel = _good_selections()
        sel["preheader"] = "x" * GraderLimits().preheader_max_chars
        assert _check(grade_selections(sel), "preheader_length").passed


class TestStoryCounts:
    def test_too_many_must_know_fails(self):
        sel = _good_selections()
        sel["must_know"] = [_article(f"Story {i}") for i in range(10)]  # default max 6
        report = grade_selections(sel)
        assert not _check(report, "story_counts_in_range").passed
        assert "must_know" in _check(report, "story_counts_in_range").detail

    def test_too_few_should_know_fails(self):
        sel = _good_selections()
        sel["should_know"] = [_article("only one")]  # default min 3
        report = grade_selections(sel)
        assert not _check(report, "story_counts_in_range").passed

    def test_configurable_ranges(self):
        sel = _good_selections()  # 2 must_know
        report = grade_selections(sel, limits=GraderLimits(must_know_range=(5, 6)))
        assert not _check(report, "story_counts_in_range").passed


class TestSourcesNonempty:
    def test_empty_sources_fails(self):
        sel = _good_selections()
        sel["must_know"][0]["sources"] = []
        report = grade_selections(sel)
        # schema_valid also fails (minItems 1), but sources_nonempty is the target.
        assert not _check(report, "sources_nonempty").passed


class TestRequiredFieldsPresent:
    def test_missing_preheader_fails(self):
        sel = _good_selections()
        del sel["preheader"]
        report = grade_selections(sel)
        assert not _check(report, "required_fields_present").passed

    def test_missing_should_know_fails(self):
        sel = _good_selections()
        del sel["should_know"]
        report = grade_selections(sel)
        assert not _check(report, "required_fields_present").passed


class TestDedupVsRecent:
    def test_exact_recent_match_fails(self):
        sel = _good_selections()
        recent = {"MUST-KNOW STORY NUMBER 0!"}  # normalizes to same as a headline
        report = grade_selections(sel, recent_titles=recent)
        assert not _check(report, "dedup_vs_recent").passed

    def test_distinct_recent_passes(self):
        sel = _good_selections()
        recent = {"A completely unrelated old headline"}
        report = grade_selections(sel, recent_titles=recent)
        assert _check(report, "dedup_vs_recent").passed


class TestPassRate:
    def test_pass_rate_partial(self):
        sel = _good_selections()
        sel["preheader"] = "x" * 200  # one check fails
        report = grade_selections(sel)
        assert 0.0 < report.pass_rate < 1.0
        assert not report.passed


class TestNoInternalArticleIds:
    """Run 247 (2026-07-28) shipped ``NYT (A316):`` into reporting_varies -- a field
    whose prompt (write.md line 96) explicitly calls those "NOT article references".
    merge now scrubs that field. headline, summary, why_it_matters and preheader come
    from the same agent under the same prompt and render verbatim, and nothing checks
    them at all: COHERENCE grades factuality, not leaks.

    This check OBSERVES; it must never edit. Reader-facing prose is precisely where a
    false positive is expensive -- "(A320)" is a real Airbus model and "(A7)" a real
    French motorway, and article ids run A1..A{n} with n in the hundreds, so no lexical
    rule separates them. Stripping a source NAME costs a parenthetical; silently
    rewriting a HEADLINE costs the headline. Across all 30 published digests these four
    fields have leaked zero times, so the evidence supports watching them, not editing
    them -- and this check is what turns the next occurrence into evidence.
    """

    @pytest.mark.parametrize("fld", ["headline", "summary", "why_it_matters"])
    def test_parenthesised_id_fails_in_every_write_field(self, fld):
        sel = _good_selections()
        sel["must_know"][0][fld] = "Quake hits Kyushu (A316) overnight"
        report = grade_selections(sel)
        check = _check(report, "no_internal_article_ids")
        assert not check.passed
        # The detail must name the field AND quote the match: deciding later whether
        # to strip needs to know if this was an article id or an aircraft.
        assert fld in check.detail
        assert "(A316)" in check.detail

    def test_bracketed_and_multi_id_forms_fail(self):
        sel = _good_selections()
        sel["must_know"][0]["summary"] = "Reports differ [A221] on the toll."
        sel["should_know"][0]["summary"] = "Sources split (A110, A263 and A349)."
        report = grade_selections(sel)
        check = _check(report, "no_internal_article_ids")
        assert not check.passed
        assert "2 leak(s)" in check.detail

    def test_preheader_leak_fails(self):
        # The preheader is the inbox preview line -- the single most-seen string
        # the pipeline emits, and top-level, so _iter_articles never reaches it.
        sel = _good_selections()
        sel["preheader"] = "Quake hits Kyushu (A316); talks stall in Doha."
        report = grade_selections(sel)
        check = _check(report, "no_internal_article_ids")
        assert not check.passed
        assert "preheader" in check.detail

    def test_reports_the_tier_and_offending_story(self):
        sel = _good_selections()
        sel["should_know"][2]["headline"] = "Talks stall in Doha (A221)"
        check = _check(grade_selections(sel), "no_internal_article_ids")
        assert "should_know" in check.detail
        assert "Talks stall in Doha" in check.detail

    def test_clean_selections_pass(self):
        check = _check(grade_selections(_good_selections()), "no_internal_article_ids")
        assert check.passed
        assert check.detail == "ok"

    @pytest.mark.parametrize(
        "summary",
        [
            # The false-positive boundary. This check does not edit text, so a hit
            # here is only a wasted log line -- but it is also the reason the guard
            # is not a stripper on these fields.
            "The council met (see annex) before the vote.",
            "Casualties (at least 40) remain unconfirmed.",
            "The vote split 6-3 (Roberts concurring).",
            "Numbered source markers [1] and [2] are not article ids.",
            # Mismatched delimiters are not a form any generator produces.
            "A split ruling [A316) stands for now.",
        ],
        ids=["annex", "number", "name", "numeric-marker", "mismatched"],
    )
    def test_legitimate_parentheses_do_not_fire(self, summary):
        sel = _good_selections()
        sel["must_know"][0]["summary"] = summary
        assert _check(grade_selections(sel), "no_internal_article_ids").passed

    def test_known_collateral_a_designators_do_fire(self):
        # KNOWN AND ACCEPTED, and the reason this check only observes: an Airbus
        # model and a French motorway are indistinguishable from an article id.
        # Here that costs one false log line. In a stripper it would cost the
        # reader the fact -- which is why headline/summary are not stripped.
        sel = _good_selections()
        sel["must_know"][0]["summary"] = "The aircraft (A320) came down near the motorway (A7)."
        assert not _check(grade_selections(sel), "no_internal_article_ids").passed


class TestLeakCheckSeesWhatActuallyLeaked:
    """The delimited pattern misses the form that caused a third of the real leaks.

    A bare id cannot be told from "the A19 chip" by pattern alone -- but a STORY declares its
    own cited article_ids, so an id in both the prose and that story's `sources` is the model
    citing itself. Same ground truth threads._clean_fact uses; free here because this check
    only observes.

    NOTE the 2026-07-12 bare leak is NOT evidence for this check: it came through the thread
    path, whose delta occupies the summary SLOT at render time and is never in `item["summary"]`
    at grading time. This detector is for WRITE doing the same in its own fields -- plausible,
    unobserved.
    """

    @staticmethod
    def _one(**over):
        item = {
            "headline": "Fire nears control",
            "summary": "Six hundred residents returned.",
            "why_it_matters": "It signals the emergency is easing.",
            "sources": [{"article_id": "A238"}],
        }
        item.update(over)
        return {"must_know": [item], "should_know": [], "preheader": "A calm preheader."}

    def _detail(self, selections):
        report = grade_selections(selections)
        return next(c for c in report.checks if c.name == "no_internal_article_ids")

    def test_flags_bare_self_citation_in_a_summary(self):
        check = self._detail(self._one(summary="Residents returned, according to A238."))
        assert not check.passed and "A238" in check.detail

    def test_ignores_an_a_designator_the_story_does_not_cite(self):
        # "A19 chip" shipped correctly on 2026-03-03 -- absent from sources, so not a citation.
        check = self._detail(self._one(summary="The iPhone 17e ships with the A19 chip."))
        assert check.passed

    def test_flags_reporting_varies_too(self):
        # merge scrubs it, but merge's scrubber is delimited-only -- so "clean by construction"
        # holds for exactly the form that is NOT the problem here.
        check = self._detail(self._one(reporting_varies=[{"source": "NYT", "angle": "Per A238.", "bias": "c"}]))
        assert not check.passed and "A238" in check.detail

    def test_survives_a_non_string_headline(self):
        # merge._grade_assembled catches Exception broadly, so any raise here loses all 11
        # checks, not just this one. See TestLeakCheckDoesNotCryWolf for the other shapes.
        report = grade_selections(self._one(headline=123))
        assert any(c.name == "no_internal_article_ids" for c in report.checks)

    def test_preheader_leak_is_reported_first_under_truncation(self):
        # The preheader is the most-seen string the pipeline emits; appended last it is the
        # first thing dropped by the offenders[:5] cap.
        sel = {
            "must_know": [
                {
                    "headline": f"Story {i}",
                    "summary": "Per (A1) and (A2) and (A3).",
                    "why_it_matters": "Why.",
                    "sources": [{"article_id": "A1"}],
                }
                for i in range(6)
            ],
            "should_know": [],
            "preheader": "Leaky preheader (A9).",
        }
        check = self._detail(sel)
        assert not check.passed
        assert "preheader" in check.detail.split("|")[0]

    def test_one_leak_is_reported_once_not_twice(self):
        # "(A316)" matches the delimited pattern AND contains a cited id; reporting both makes
        # the count lie about how many distinct leaks there are.
        check = self._detail(self._one(sources=[{"article_id": "A316"}], summary="Reports it (A316) plainly."))
        assert not check.passed
        assert check.detail.startswith("1 leak(s):"), check.detail


class TestLeakCheckDoesNotCryWolf:
    """A log-only check earns its keep only if a clean run is silent.

    Every input here is schema-valid or reaches a caller that does not validate at all
    (bin/eval grades arbitrary JSON with no try/except; test_prompt catches and loses the whole
    report). merge._grade_assembled swallows Exception broadly, so one raise here costs all 11
    checks, not just this one.
    """

    @staticmethod
    def _sel(**over):
        item = {
            "headline": "Fire nears control",
            "summary": "Six hundred residents returned.",
            "why_it_matters": "It signals the emergency is easing.",
            "sources": [{"article_id": "A238"}],
        }
        item.update(over)
        return {"must_know": [item], "should_know": [], "preheader": "A calm preheader."}

    def _check(self, sel):
        return next(c for c in grade_selections(sel).checks if c.name == "no_internal_article_ids")

    @pytest.mark.parametrize("aid", ["", "   "], ids=["empty", "whitespace"])
    def test_blank_article_id_is_not_three_phantom_leaks(self, aid):
        # SOURCE_SCHEMA has no minLength, so this is schema-valid. re.escape("") is \b\b, which
        # matches at every word boundary -- a clean story reported as leaking ''.
        assert self._check(self._sel(sources=[{"article_id": aid}])).passed

    @pytest.mark.parametrize("bad", [5, "A3", None, {"a": 1}], ids=["int", "scalar-str", "none", "dict"])
    def test_non_list_sources_does_not_discard_every_check(self, bad):
        report = grade_selections(self._sel(sources=bad))
        assert len(report.checks) > 1, "a raise here loses all 11 checks, not just this one"

    @pytest.mark.parametrize("bad", [5, "x", {"a": 1}], ids=["int", "str", "dict"])
    def test_non_list_reporting_varies_does_not_raise(self, bad):
        report = grade_selections(self._sel(reporting_varies=bad))
        assert any(c.name == "no_internal_article_ids" for c in report.checks)

    def test_padded_source_id_is_still_detected(self):
        # threads._clean_fact strips ids for exactly this reason; diverging means the two
        # implementations disagree about the same leak.
        check = self._check(self._sel(sources=[{"article_id": "A238 "}], summary="Per A238 today."))
        assert not check.passed

    def test_same_id_delimited_and_bare_counts_as_two_places(self):
        # The count is what tells a human they have found them all; collapsing these to one
        # points at the delimited match and hides the bare one.
        check = self._check(self._sel(summary="Differ (A238) and again per A238."))
        assert check.detail.startswith("2 leak(s):"), check.detail

    def test_bare_leak_in_the_preheader_is_detected(self):
        # The preheader is emitted first precisely because it is the most-seen string; covering
        # it with only one of the two detectors is a strange place to stop.
        sel = self._sel()
        sel["preheader"] = "Fire nears control, according to A238."
        assert not self._check(sel).passed


class TestLimitsAreCalibratedNotAspirational:
    """GraderLimits' own docstring: defaults are "set generously around current observed volume
    so they don't fail spuriously". Measured over 40 shipped runs / 635 stories (runs 241-280)
    the 80-word summary cap fired on 38.4% and the 60-word why cap on 22.2% -- a check that
    rejects a third of healthy output is a broken instrument, not a signal. Caps now sit at the
    p99 of shipped output so they catch outliers."""

    def test_preheader_cap_is_derived_from_the_schema(self):
        """Not restated. The comment claimed "matches SELECTIONS_SCHEMA maxLength" while the
        schema said 157 and the grader 150 -- 497a05b raised the schema and called itself "the
        single source of truth". Deriving makes the drift unrepresentable."""
        from schema import PREHEADER_MAX_CHARS, SELECTIONS_SCHEMA

        assert GraderLimits().preheader_max_chars == PREHEADER_MAX_CHARS
        assert SELECTIONS_SCHEMA["properties"]["preheader"]["maxLength"] == PREHEADER_MAX_CHARS

    def test_caps_clear_the_p99_of_shipped_output(self):
        """p99 over runs 241-280: summary 119, why 77, headline 19. A cap below its own p99
        fires on healthy output every run."""
        lim = GraderLimits()
        assert lim.summary_max_words >= 119
        assert lim.why_it_matters_max_words >= 77
        assert lim.headline_max_words >= 19
