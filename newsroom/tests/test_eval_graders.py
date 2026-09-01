"""Tests for eval_graders.py (L1 binary code-assertion graders).

Each test proves a single check fires correctly: a known-good fixture passes
everything, and targeted broken fixtures make exactly the relevant check fail.
No network, no DB -- small inline fixtures only.
"""

import sys
from fractions import Fraction
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
            "why_it_matters_restates_summary",
            "why_it_matters_sentence_count",
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
        assert len(report.checks) > 1, "a raise here loses every other check, not just this one"

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


class TestWhyItMattersSentenceCount:
    """write.md tells WRITE why_it_matters is "One sentence" and nothing graded it. The
    spec is the cap here -- unlike the word caps, which sit at a percentile because no
    spec exists. Measured over 1149 shipped lines (runs 204-285) the spec normally holds:
    98.6% one sentence, 1.4% two, none longer."""

    def _sentences(self, sel):
        return _check(grade_selections(sel), "why_it_matters_sentence_count")

    def test_one_sentence_passes(self):
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = "The ruling removes the last domestic barrier to the merger."
        assert self._sentences(sel).passed

    def test_at_the_cap_passes_and_one_sentence_over_fails(self):
        """Derived from the cap, not restated."""
        cap = GraderLimits().why_it_matters_max_sentences
        sel = _good_selections()
        sel["should_know"][0]["why_it_matters"] = " ".join(f"Sentence number {i} says something." for i in range(cap))
        assert self._sentences(sel).passed

        sel["should_know"][0]["why_it_matters"] = " ".join(
            f"Sentence number {i} says something." for i in range(cap + 1)
        )
        check = self._sentences(sel)
        assert not check.passed
        assert "should_know" in check.detail

    def test_configurable_cap_respected(self):
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = "The vote settles leadership. It does not settle the budget."
        assert not self._sentences(sel).passed
        loose = GraderLimits(why_it_matters_max_sentences=2)
        assert _check(grade_selections(sel, limits=loose), "why_it_matters_sentence_count").passed

    @pytest.mark.parametrize(
        "why",
        [
            "The U.S. decision reshapes the alliance for a decade.",
            "It matters because the U.S. and the U.K. now diverge on enforcement.",
            "Sept. 11 remains the reference point for the whole doctrine.",
            "Dr. Ahmed's finding undercuts the ministry's published timeline.",
            "The vote at 9 a.m. settles nothing about the succession.",
            "Talks resume Nov. 3 under No. 10's new negotiating terms.",
        ],
        ids=["us", "us-uk", "month", "title", "am", "no-and-month"],
    )
    def test_abbreviations_are_not_sentence_ends(self, why):
        """The whole risk in a naive split. A naive `[.?!]\\s+` splitter reads all six as
        multi-sentence; over the 1149 shipped lines it over-counts 9 and under-counts none,
        and all 10 of its spurious splits are "U.S."."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = why
        assert self._sentences(sel).passed

    @pytest.mark.parametrize(
        "why",
        [
            "The vote settles the leadership question. It does not settle the budget.",
            "Why now? Because the tariff waiver lapses on Friday.",
            "Washington blinked! Beijing did not.",
            "The grade was an A. The school objected anyway.",
        ],
        ids=["period", "question", "bang", "single-letter-then-stop"],
    )
    def test_genuine_sentence_breaks_are_still_counted(self, why):
        """The single-letter case is why the initialism guard requires TWO letter-dot pairs:
        masking a lone "A." would swallow a real sentence end and make the check under-count,
        which is the failure direction that goes unnoticed."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = why
        assert not self._sentences(sel).passed

    def test_a_terminator_inside_a_closing_quote_still_ends_the_sentence(self):
        """The terminator is not the last character: `."` puts a quote between the stop and
        the space. Found by stress-testing the splitter, not by the corpus -- shipped output
        happens not to contain it, and the whole distribution is unchanged by the fix."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = 'She called it "a disgrace." The ministry disagreed.'
        assert not self._sentences(sel).passed

    @pytest.mark.parametrize(
        "why",
        [
            "Prices rose in the U.S. Europe saw no change at all.",
            "The deal collapsed at 3 p.m. Talks resume again on Monday.",
            "the vote settled the leadership. the budget did not follow.",
            "The answer from the ministry was no. The vote proceeds anyway.",
            "It changed the state of the art. Nobody in the sector noticed.",
        ],
        ids=[
            "initialism-then-capital",
            "lowercase-abbrev-then-capital",
            "lowercase-opener",
            "title-list-collides-with-a-word",
            "title-list-collides-with-a-word-2",
        ],
    )
    def test_known_under_counts_are_recorded_not_claimed_fixed(self, why):
        """Inputs the splitter reads as ONE sentence when they are two. Under-counting is the
        silent direction, so they are pinned rather than left to be discovered.

        Three distinct causes, not one. The initialism cases are genuinely ambiguous --
        nothing lexical separates "the U.S. Europe saw" from "the U.S. Europe policy", and
        that needs a model. The lowercase opener cannot arise from WRITE, which capitalises.
        The last two are neither: _TITLE_ABBREVIATION is wrapped in (?i:) so "no." and "art."
        mask ordinary English words, which is unambiguous, WRITE-reachable, and fixable by
        dropping the case-insensitivity for the colliding entries. It is unfixed because the
        base rate is zero -- that pattern matches none of the 1149 shipped why_it_matters
        lines, the only field this check reads. If a fix lands, this test flips to the
        positive assertion."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = why
        assert self._sentences(sel).passed, "splitter improved -- move this case to the positive test"

    @pytest.mark.parametrize(
        "why",
        [
            'Trump said the ceasefire is "dead. Over. Finished." for Tehran.',
            "The ruling cites Art. 5, Sec. 3 and Para. 12 of the treaty text.",
            "Robert F. Kennedy Jr. now leads the agency regulating the vaccines he sued over.",
        ],
        ids=["terminator-inside-quotation", "abbreviations-not-in-the-list", "middle-initial"],
    )
    def test_known_over_counts_are_recorded_not_claimed_fixed(self, why):
        """The COST side of three deliberate choices, pinned so none reads as free.

        Letting a closing quote sit between the terminator and the space buys the
        '"a disgrace." The ministry' case above and costs a quotation containing its own full
        stops. The abbreviation list's edge carries Art./No./Vol./Fig. but not
        Sec./Para./Ave./Rd., and every addition widens the under-count risk pinned above.
        Requiring TWO letter-dot pairs keeps the sentence end in "an A. The school" and costs
        a middle initial -- and unlike the lowercase-opener under-count this one is reachable
        from WRITE, since sitting officials have middle initials.

        None of the three occurs in the 1149 shipped lines, and all 16 lines the check flags
        there are genuine two-sentence prose."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = why
        assert not self._sentences(sel).passed, "splitter improved -- move this case to the negative test"

    def test_non_string_why_does_not_raise(self):
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = None
        assert self._sentences(sel).passed

    def test_a_run_235_line_no_other_check_covers_is_caught(self):
        """Run 235 shipped 10 of its 18 stories at two sentences. This is one of the six that
        are UNDER the 80-word cap (56 words), verbatim -- so it is coverage no existing check
        provides.

        The obvious pick, run 235's longest two-sentence line, is 82 words and already fails
        why_it_matters_length. A fixture that another check catches proves nothing about this
        one."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = (
            "The Starmer government completed the nationalisation days before leaving office, "
            "meaning Burnham inherits an asset that requires a credible industrial plan. Without "
            "a procurement commitment to buy British steel for public infrastructure projects — "
            "as unions explicitly demanded — the nationalisation risks becoming an expensive "
            "holding operation rather than a durable revival of domestic steelmaking capacity."
        )
        report = grade_selections(sel)
        assert not _check(report, "why_it_matters_sentence_count").passed
        assert _check(report, "why_it_matters_length").passed, "fixture must not be covered by the word cap"


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

    def test_restatement_cap_clears_simulated_repair_output_not_just_shipped_output(self):
        """Shipped containment over 1149 stories (runs 204-285) is p50 0.176 / p99 0.375 /
        max 0.500, but shipped output is PRE-repair and is not the binding constraint.
        repair.md deletes the unsupported specific, which is by definition absent from the
        summary, so repair can only raise containment. Simulated over the same corpus
        (n=632) a correctly repaired line reaches 0.647. A cap must clear THAT, not 0.500 --
        0.65 would have cleared it by 0.003, which is luck rather than calibration."""
        assert GraderLimits().why_restatement_max_overlap > 0.647
        # Below the lowest hand-written restatement measured (0.750 against run 265).
        assert GraderLimits().why_restatement_max_overlap < 0.750

    def test_the_sentence_cap_matches_the_write_spec(self):
        """The cap is not an independent judgement -- it restates a rule WRITE is already
        given. If write.md is relaxed to two sentences and this cap is not, the grader
        starts failing compliant output; if the cap is relaxed and the prompt is not,
        the grader stops enforcing the prompt. Fail here rather than in either direction."""
        spec = (Path(__file__).parent.parent.parent / ".claude/agents/write.md").read_text(encoding="utf-8")
        why_rule = next(ln for ln in spec.splitlines() if ln.startswith("**Why it matters"))
        assert "One sentence" in why_rule
        assert GraderLimits().why_it_matters_max_sentences == 1


def _sized_why(shared: int, novel: int) -> tuple[str, str]:
    """(summary, why) whose content-token overlap is exactly shared / (shared + novel).

    Both halves are coined words so nothing collides with a stop word, with each
    other, or with punctuation handling.
    """
    shared_words = [f"sharedtoken{i}" for i in range(shared)]
    novel_words = [f"noveltoken{i}" for i in range(novel)]
    # Every carrier word must be a stop word or the overlap is not what this says. "It
    # matters that ..." put "matters" -- a content token absent from the summary -- into
    # the denominator, so the at-the-cap case scored 0.636 against a 0.70 cap and the
    # pass-side assertion never touched the boundary. A `>` to `>=` mutation survived the
    # whole suite. That is exactly the defect b4ca27d named, one level further in.
    summary = "It is that " + " ".join(shared_words) + " now."
    why = "It is that " + " ".join(shared_words + novel_words) + " now."
    return summary, why


def _overlap_denominator(cap: float) -> int:
    """Token count that expresses `cap` EXACTLY, so the boundary tests derive from it.

    From the decimal repr, not Fraction(cap).limit_denominator(1000): that rounds 0.6666 to
    2/3, which would silently move the at-the-cap case above the cap and test the wrong side
    of the boundary the moment someone retunes the limit.
    """
    frac = Fraction(str(cap))
    assert frac.numerator / frac.denominator == cap, f"{cap} is not an exact decimal"
    return frac.denominator


# Run 233's shipped must_know story, verbatim: the highest content-token containment of
# why_it_matters in its own summary across 1149 shipped stories (runs 204-285).
_RUN_233_STORY = {
    "summary": (
        "A late-night fire at Rong Beer Na Lat Phrao bar in Bangkok's Chatuchak district killed at "
        "least 30 people and injured more than 70, with 24 remaining in critical condition; police "
        "say blocked rear exits and flammable decorative materials — including plastic flowers and "
        "foam ceilings — are the primary theory for the high death toll. Investigators found a table "
        "blocking one exit near the restrooms, where most victims were found, and a second exit with "
        "a broken handle and damaged signage; preliminary findings point to an electrical short "
        "circuit in a ceiling air conditioner as the likely ignition source. The bar was registered "
        "as a restaurant with live music rather than an entertainment venue, exempting it from "
        "requirements to use fire-retardant materials."
    ),
    "why_it_matters": (
        "The venue's classification as a 'restaurant with live music' rather than an 'entertainment "
        "venue' let it avoid fire-retardant material requirements — a legal gap Bangkok authorities "
        "have now said they will review — meaning the death toll was shaped as much by a regulatory "
        "category as by the fire itself."
    ),
}


class TestWhyItMattersRestatesSummary:
    """The degenerate REPAIR of a flagged why_it_matters is to delete the analysis and
    restate the summary in importance-language. coherence.md does not fail analytical
    content, so such a repair passes the re-check by construction and is recorded as a
    success. This check is the only thing that sees it."""

    def _overlap_check(self, sel):
        return _check(grade_selections(sel), "why_it_matters_restates_summary")

    def test_a_flattened_why_it_matters_fails(self):
        sel = _good_selections()
        summary, why = _sized_why(shared=20, novel=1)
        sel["must_know"][0]["summary"] = summary
        sel["must_know"][0]["why_it_matters"] = why
        check = self._overlap_check(sel)
        assert not check.passed
        assert "must_know" in check.detail

    def test_the_failure_detail_does_not_print_false_arithmetic(self):
        """The detail line is this check's only production visibility. Rounded to 2 dp a
        genuine failure printed "(0.70 > 0.70)", because the comparison is full-precision and
        the display was not. Derived from the cap: pick an overlap that rounds DOWN to it."""
        cap = GraderLimits().why_restatement_max_overlap
        # Search for the smallest denominator admitting an overlap just above the cap rather
        # than assuming one. A fixed multiple of _overlap_denominator happens to work at 0.70
        # and raises StopIteration at 0.65 and 0.75 -- both values this file's own calibration
        # test puts in play -- which would fail a valid retune in a language nobody can read.
        total, shared = next(
            (t, n) for t in range(2, 4000) if (n := next((m for m in range(t + 1) if cap < m / t < cap + 0.005), None))
        )
        summary, why = _sized_why(shared=shared, novel=total - shared)
        sel = _good_selections()
        sel["must_know"][0]["summary"] = summary
        sel["must_know"][0]["why_it_matters"] = why
        check = self._overlap_check(sel)
        assert not check.passed
        assert f"{cap:.2f} > {cap:.2f}" not in check.detail, check.detail

    def test_at_the_cap_passes_and_one_token_over_fails(self):
        """Boundary DERIVED from the cap. Restating it as a literal is the defect b4ca27d
        found: the test silently stops exercising the boundary the moment the cap moves."""
        cap = GraderLimits().why_restatement_max_overlap
        total = _overlap_denominator(cap)
        at_cap = round(cap * total)

        sel = _good_selections()
        summary, why = _sized_why(shared=at_cap, novel=total - at_cap)
        sel["should_know"][0]["summary"] = summary
        sel["should_know"][0]["why_it_matters"] = why
        assert self._overlap_check(sel).passed

        summary, why = _sized_why(shared=at_cap + 1, novel=total - at_cap - 1)
        sel["should_know"][0]["summary"] = summary
        sel["should_know"][0]["why_it_matters"] = why
        assert not self._overlap_check(sel).passed

    def test_configurable_cap_respected(self):
        sel = _good_selections()
        summary, why = _sized_why(shared=1, novel=3)
        sel["must_know"][0]["summary"] = summary
        sel["must_know"][0]["why_it_matters"] = why
        assert self._overlap_check(sel).passed
        tight = GraderLimits(why_restatement_max_overlap=0.1)
        assert not _check(grade_selections(sel, limits=tight), "why_it_matters_restates_summary").passed

    def test_an_empty_why_it_matters_does_not_fire(self):
        """Blanking is BLANKED_WHY_IT_MATTERS' rule and no_empty_strings' check. A zero
        denominator scoring 1.0 here would make two rules fire on one fault and bury the
        one that names it."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = "   "
        assert self._overlap_check(sel).passed

    def test_non_string_fields_do_not_raise(self):
        """merge._grade_assembled swallows a grader exception and loses the WHOLE report,
        so a check that raises disables every other check, not just this one."""
        sel = _good_selections()
        sel["must_know"][0]["why_it_matters"] = None
        sel["should_know"][0]["summary"] = 42
        assert self._overlap_check(sel).passed

    def test_the_highest_overlap_line_ever_shipped_still_passes(self):
        """Run 233's Bangkok bar fire story, verbatim: the maximum content-token containment
        (0.500) over 1149 shipped stories, runs 204-285. It reuses the summary's entities to
        make an argument the summary does not make, which is what an analytic line looks
        like. A cap that fails it is a cap that rejects the product.

        Quoted in full deliberately. An abridged summary scores 0.429, which would leave the
        test passing while no longer pinning the maximum it names."""
        sel = _good_selections()
        sel["must_know"][0].update(_RUN_233_STORY)
        assert self._overlap_check(sel).passed

    def test_the_cap_does_not_depend_on_the_stop_list(self, monkeypatch):
        """The stop list is a free parameter baked into production. Recomputed over the same
        1149 shipped stories, dropping it ENTIRELY moves p99 0.375 -> 0.440 and the maximum
        0.500 -> 0.571, and still leaves zero stories over the cap. The threshold rests on
        the gap between the two populations, not on which words were picked."""
        import eval_graders

        monkeypatch.setattr(eval_graders, "_STOP_WORDS", frozenset())
        sel = _good_selections()
        sel["must_know"][0].update(_RUN_233_STORY)
        assert self._overlap_check(sel).passed
