"""Tests for eval_graders.py (L1 binary code-assertion graders).

Each test proves a single check fires correctly: a known-good fixture passes
everything, and targeted broken fixtures make exactly the relevant check fail.
No network, no DB -- small inline fixtures only.
"""

import sys
from pathlib import Path

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
    def test_over_150_chars_fails(self):
        sel = _good_selections()
        sel["preheader"] = "x" * 151
        report = grade_selections(sel)
        assert not _check(report, "preheader_length").passed


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
