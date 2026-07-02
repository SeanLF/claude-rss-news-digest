"""Tests for merge.py (post-dispatcher selections assembly) and schema validation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from merge import assemble_selections
from schema import validate_selections


def _article(headline, article_id="A1"):
    return {
        "headline": headline,
        "summary": "Summary.",
        "why_it_matters": "Why.",
        "sources": [{"article_id": article_id}],
    }


def _draft(must_know=None, should_know=None, preheader="Preheader."):
    return {
        "must_know": must_know or [],
        "should_know": should_know or [],
        "preheader": preheader,
    }


def _coherence(*results):
    return {"results": list(results)}


def _write(tmp_path, draft, coherence, clusters=None, selected=None):
    (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
    (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))
    if clusters is not None:
        (tmp_path / "clusters.json").write_text(json.dumps(clusters))
    if selected is not None:
        (tmp_path / "selected.json").write_text(selected if isinstance(selected, str) else json.dumps(selected))


class TestAssembleSelections:
    def test_drops_failed_must_know(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False},
        )
        _write(tmp_path, draft, coherence)

        out = assemble_selections(tmp_path)
        assembled = json.loads(out.read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_drops_failed_should_know(self, tmp_path):
        draft = _draft(
            must_know=[_article("mk")],
            should_know=[_article("keep"), _article("drop")],
        )
        coherence = _coherence(
            {"headline": "mk", "pass": True},
            {"headline": "keep", "pass": True},
            {"headline": "drop", "pass": False},
        )
        _write(tmp_path, draft, coherence)

        out = assemble_selections(tmp_path)
        assembled = json.loads(out.read_text())

        assert [a["headline"] for a in assembled["should_know"]] == ["keep"]

    def test_pass_missing_from_report_entirely_defaults_to_keep(self, tmp_path):
        # If coherence's results list has NO entry at all for a headline (a
        # coverage gap, not a malformed entry), treat it as passing -- this is
        # the "no entry" case, distinct from an entry present with a bad/missing
        # "pass" value (see TestStrictPassSemantics).
        draft = _draft(must_know=[_article("missing-from-coherence")])
        _write(tmp_path, draft, _coherence())

        out = assemble_selections(tmp_path)
        assembled = json.loads(out.read_text())

        assert len(assembled["must_know"]) == 1

    def test_writes_pretty_json(self, tmp_path):
        draft = _draft(must_know=[_article("h")])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        out = assemble_selections(tmp_path)

        assert out.name == "selections.json"
        assert "\n" in out.read_text()  # indented

    def test_raises_on_missing_draft(self, tmp_path):
        (tmp_path / "coherence_report.json").write_text(json.dumps(_coherence()))
        with pytest.raises(RuntimeError, match=r"draft_selections\.json missing"):
            assemble_selections(tmp_path)

    def test_raises_on_missing_coherence(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft()))
        with pytest.raises(RuntimeError, match=r"coherence_report\.json missing"):
            assemble_selections(tmp_path)

    def test_raises_when_must_know_empty_after_drops(self, tmp_path):
        # Every must_know fails coherence -> empty list -> raise (no empty broadcast).
        draft = _draft(must_know=[_article("a"), _article("b")])
        coherence = _coherence(
            {"headline": "a", "pass": False},
            {"headline": "b", "pass": False},
        )
        _write(tmp_path, draft, coherence)

        with pytest.raises(RuntimeError, match="no must_know"):
            assemble_selections(tmp_path)

    def test_raises_when_must_know_empty_in_draft(self, tmp_path):
        # Draft itself has no must_know -> raise before broadcasting anything.
        _write(tmp_path, _draft(should_know=[_article("x")]), _coherence())

        with pytest.raises(RuntimeError, match="no must_know"):
            assemble_selections(tmp_path)

    def test_raises_when_must_know_key_missing(self, tmp_path):
        # Defensive: draft missing the must_know key entirely (None) still raises cleanly.
        draft = _draft(should_know=[_article("x")])
        del draft["must_know"]
        _write(tmp_path, draft, _coherence())

        with pytest.raises(RuntimeError, match="no must_know"):
            assemble_selections(tmp_path)

    def test_raises_on_schema_violation(self, tmp_path):
        draft = _draft(must_know=[{"headline": "missing required fields"}])
        _write(tmp_path, draft, _coherence())

        with pytest.raises(RuntimeError, match="schema validation"):
            assemble_selections(tmp_path)


class TestStrictPassSemantics:
    """Only the literal boolean True counts as a pass. The report is
    model-generated JSON, so non-boolean "pass" values (JSON-encoded as a
    string) or an omitted "pass" key on a present entry are plausible drift.
    Both must be treated as FAILED (conservative drop), not silently kept -- matching the
    module's "over-dropping is safer than silently keeping an unverified
    headline" stance. A warning must name the story and the offending value."""

    def test_string_false_drops_and_warns(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": "false"},  # string, not bool -- TRUTHY if used raw
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]
        assert any("bad" in r.getMessage() for r in caplog.records)

    def test_string_true_drops_and_warns(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": "true"},  # string, not bool
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]
        assert any("bad" in r.getMessage() for r in caplog.records)

    def test_entry_present_but_missing_pass_key_drops_and_warns(self, tmp_path, caplog):
        # An entry that DOES exist for the headline but omits "pass" entirely --
        # distinct from a coverage gap (no entry at all), which still defaults
        # to keep (see test_pass_missing_from_report_entirely_defaults_to_keep).
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad"},  # no "pass" key
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]
        assert any("bad" in r.getMessage() for r in caplog.records)

    def test_boolean_false_still_drops(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_boolean_true_still_keeps(self, tmp_path):
        draft = _draft(must_know=[_article("good")])
        coherence = _coherence({"headline": "good", "pass": True})
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]


class TestFieldAwareCoherenceDegradation:
    """A why_it_matters-only coherence failure should NOT drop the whole story --
    WRITE habitually seasons why_it_matters with true-but-uncited background
    specifics ("6-3", "$60bn"), and that pattern was dropping up to 35% of
    stories on a real archived day. Instead: blank why_it_matters and keep the
    rest of the story. ANY other shape (mixed fields, absent, empty, unparseable,
    unknown names) stays a full drop -- conservative default, over-drop beats
    shipping fabrication."""

    def test_why_it_matters_only_failure_keeps_story_with_blanked_field(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("bad")])
        coherence = _coherence(
            {
                "headline": "bad",
                "pass": False,
                "reason": "why_it_matters: cites a 6-3 vote not in sources",
                "failed_fields": ["why_it_matters"],
            }
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["bad"]
        assert assembled["must_know"][0]["why_it_matters"] == ""
        assert any(
            "coherence stripped why_it_matters" in r.getMessage() and "bad" in r.getMessage() for r in caplog.records
        )

    def test_mixed_failed_fields_drops(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {
                "headline": "bad",
                "pass": False,
                "reason": "summary + why_it_matters both unsupported",
                "failed_fields": ["summary", "why_it_matters"],
            },
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_absent_failed_fields_drops(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False, "reason": "why_it_matters: unsupported"},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_unknown_field_name_drops(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {
                "headline": "bad",
                "pass": False,
                "reason": "some other field failed",
                "failed_fields": ["preheader"],
            },
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_empty_failed_fields_list_drops(self, tmp_path):
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False, "reason": "unclear", "failed_fields": []},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_unparseable_failed_fields_drops(self, tmp_path):
        # Defensive: not a list at all (e.g. malformed/legacy shape). merge.py
        # must not crash and must fall back to the conservative full drop.
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False, "reason": "unclear", "failed_fields": "why_it_matters"},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_mixed_type_failed_fields_drops(self, tmp_path):
        # A list whose entries are not ALL strings is unparseable -- it must NOT
        # be silently filtered down to {"why_it_matters"} and given the softer
        # strip treatment. Conservative full drop (defense in depth: the
        # orchestrate gate rejects this shape, but merge.py must not rely on it).
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False, "reason": "why_it_matters: x", "failed_fields": ["why_it_matters", 42]},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_two_failing_results_one_why_only_one_summary_drops(self, tmp_path):
        # A story matched by TWO failing results -- one why_it_matters-only,
        # one naming summary -- must take the conservative full drop (the
        # all() over hits is the load-bearing line).
        draft = _draft(must_know=[_article("good"), _article("bad")])
        coherence = _coherence(
            {"headline": "good", "pass": True},
            {"headline": "bad", "pass": False, "reason": "why_it_matters: x", "failed_fields": ["why_it_matters"]},
            {"headline": "bad", "pass": False, "reason": "summary: y", "failed_fields": ["summary"]},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["good"]

    def test_coerced_nonboolean_pass_with_why_only_failed_fields_still_strips(self, tmp_path, caplog):
        # The coercion in _coherence_failed is about the "pass" VALUE, not the
        # fields -- a non-True pass ("false" the string) that nonetheless
        # carries a usable why_it_matters-only failed_fields should still strip
        # rather than drop.
        draft = _draft(must_know=[_article("bad")])
        coherence = _coherence(
            {
                "headline": "bad",
                "pass": "false",  # coerced fail, not a strict boolean
                "reason": "why_it_matters: unsupported specific",
                "failed_fields": ["why_it_matters"],
            }
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert [a["headline"] for a in assembled["must_know"]] == ["bad"]
        assert assembled["must_know"][0]["why_it_matters"] == ""


class TestClusterIdMapping:
    def test_attaches_cluster_id_from_clusters_json(self, tmp_path):
        draft = _draft(
            must_know=[_article("mk", article_id="A267")],
            should_know=[_article("sk", article_id="A354")],
        )
        coherence = _coherence(
            {"headline": "mk", "pass": True},
            {"headline": "sk", "pass": True},
        )
        clusters = {
            "clusters": [
                {"story": "Iran Missile Attacks on Gulf States", "article_ids": ["A267"]},
                {"story": "Energy Crisis Impact on Asian Markets", "article_ids": ["A354", "A999"]},
            ]
        }
        _write(tmp_path, draft, coherence, clusters=clusters)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["cluster_id"] == "Iran Missile Attacks on Gulf States"
        assert assembled["should_know"][0]["cluster_id"] == "Energy Crisis Impact on Asian Markets"

    def test_no_cluster_id_when_clusters_json_missing(self, tmp_path):
        # Best-effort: missing clusters.json must not break assembly.
        draft = _draft(must_know=[_article("mk", article_id="A1")])
        _write(tmp_path, draft, _coherence({"headline": "mk", "pass": True}))

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "cluster_id" not in assembled["must_know"][0]

    def test_no_cluster_id_when_article_unmapped(self, tmp_path):
        # Article not present in any cluster -> no cluster_id, still valid.
        draft = _draft(must_know=[_article("mk", article_id="A1")])
        clusters = {"clusters": [{"story": "Other", "article_ids": ["A2"]}]}
        _write(tmp_path, draft, _coherence({"headline": "mk", "pass": True}), clusters=clusters)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "cluster_id" not in assembled["must_know"][0]

    def test_malformed_clusters_json_does_not_break(self, tmp_path):
        draft = _draft(must_know=[_article("mk", article_id="A1")])
        (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
        (tmp_path / "coherence_report.json").write_text(json.dumps(_coherence({"headline": "mk", "pass": True})))
        (tmp_path / "clusters.json").write_text("{ not valid json")

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "cluster_id" not in assembled["must_know"][0]


class TestNonFatalGraderHook:
    """The L1 graders run after schema validation as a NON-FATAL assertion:
    failures are logged but must never abort assembly."""

    def test_grader_failure_logs_warning_but_does_not_raise(self, tmp_path, caplog):
        # 90-word summary blows the 80-word cap -> summary_length check fails.
        long_summary = "word " * 90
        item = _article("h")
        item["summary"] = long_summary.strip()
        draft = _draft(must_know=[item])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            out = assemble_selections(tmp_path)  # must NOT raise

        assert out.exists()
        assembled = json.loads(out.read_text())
        assert len(assembled["must_know"]) == 1  # output still written
        # The failing check surfaced as a warning.
        assert any("summary_length" in r.message or "summary_length" in str(r.args) for r in caplog.records)
        assert any("non-fatal" in r.getMessage() for r in caplog.records)

    def test_clean_output_logs_all_checks_passed(self, tmp_path, caplog):
        draft = _draft(
            must_know=[_article("h")], should_know=[_article("s", "A2"), _article("t", "A3"), _article("u", "A4")]
        )
        _write(
            tmp_path,
            draft,
            _coherence(
                {"headline": "h", "pass": True},
                {"headline": "s", "pass": True},
                {"headline": "t", "pass": True},
                {"headline": "u", "pass": True},
            ),
        )

        with caplog.at_level("INFO", logger="merge"):
            assemble_selections(tmp_path)

        assert any("all" in r.getMessage() and "checks passed" in r.getMessage() for r in caplog.records)

    def test_grader_exception_is_swallowed(self, tmp_path, caplog, monkeypatch):
        # If the graders themselves raise, instrumentation must not break the run.
        import merge

        def _boom(*_a, **_kw):
            raise ValueError("grader exploded")

        monkeypatch.setattr(merge, "grade_selections", _boom)
        draft = _draft(must_know=[_article("h")])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            out = assemble_selections(tmp_path)  # must NOT raise

        assert out.exists()
        assert any("graders errored" in r.getMessage() for r in caplog.records)


class TestValidateSelections:
    def _valid(self):
        return _draft(must_know=[_article("h")])

    def test_valid_passes(self):
        assert validate_selections(self._valid()) == []

    def test_rejects_string_must_know(self):
        bad = self._valid()
        bad["must_know"] = "[]"
        errors = validate_selections(bad)
        assert any("must_know" in e and "array" in e for e in errors)

    def test_rejects_missing_required(self):
        bad = {"must_know": []}
        errors = validate_selections(bad)
        assert any("should_know" in e for e in errors)
        assert any("preheader" in e for e in errors)

    def test_rejects_old_source_schema(self):
        bad = self._valid()
        bad["must_know"][0]["sources"] = [{"name": "BBC", "url": "https://bbc.com", "bias": "center"}]
        errors = validate_selections(bad)
        assert errors

    def test_rejects_unknown_top_level_key(self):
        bad = self._valid()
        bad["signals"] = {"americas": []}
        errors = validate_selections(bad)
        assert errors

    def test_accepts_not_covered_blurb(self):
        good = self._valid()
        good["not_covered_blurb"] = "Skipped a minor sports story."
        assert validate_selections(good) == []

    def test_valid_without_not_covered_blurb(self):
        # Optional -- omitting it entirely must still pass.
        assert validate_selections(self._valid()) == []

    def test_rejects_overlong_not_covered_blurb(self):
        bad = self._valid()
        bad["not_covered_blurb"] = "x" * 301
        errors = validate_selections(bad)
        assert any("not_covered_blurb" in e for e in errors)


class TestCoherenceMatching:
    """Coherence-fail matching must survive the WRITE/COHERENCE headline-string drift.

    COHERENCE re-types each headline into its report; the model may reword it. The
    drop decision must therefore key on the stable opaque article_ids, not the
    free-text headline, so a pass:false entry reliably drops its target instead of
    silently keeping an unverified headline.
    """

    def test_drops_by_article_ids_despite_reworded_headline(self, tmp_path):
        draft = _draft(
            must_know=[_article("Aid convoy reaches camp", "A1")],
            should_know=[_article("Strike kills 12 in Gaza", "A5")],
        )
        # COHERENCE flags the A5 story but rewords the headline string entirely.
        coherence = _coherence(
            {"headline": "Aid convoy reaches camp", "article_ids": ["A1"], "pass": True},
            {"headline": "Gaza strike kills twelve people", "article_ids": ["A5"], "pass": False},
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert [a["headline"] for a in assembled["must_know"]] == ["Aid convoy reaches camp"]
        assert assembled["should_know"] == [], "reworded pass:false headline must still drop via article_ids"

    def test_warns_when_fail_matches_no_headline(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("kept story", "A1")])
        coherence = _coherence(
            {"headline": "kept story", "article_ids": ["A1"], "pass": True},
            {"headline": "ghost story", "article_ids": ["A99"], "pass": False},
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert [a["headline"] for a in assembled["must_know"]] == ["kept story"]
        assert any("ghost story" in r.message for r in caplog.records), "a fail that drops nothing must be surfaced"

    def test_warns_on_coverage_gap(self, tmp_path, caplog):
        draft = _draft(
            must_know=[_article("checked", "A1")],
            should_know=[_article("unchecked", "A2")],
        )
        coherence = _coherence({"headline": "checked", "article_ids": ["A1"], "pass": True})
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            json.loads(assemble_selections(tmp_path).read_text())
        assert any("unchecked" in r.message for r in caplog.records), (
            "a headline with no coherence entry must be surfaced"
        )

    def test_headline_fallback_still_drops_when_no_article_ids(self, tmp_path):
        # Legacy-shaped report (no article_ids): normalized-headline match must still drop.
        draft = _draft(must_know=[_article("keep me", "A1"), _article("drop me", "A2")])
        coherence = _coherence(
            {"headline": "keep me", "pass": True},
            {"headline": "Drop me.", "pass": False},  # case + trailing-dot drift
        )
        _write(tmp_path, draft, coherence)

        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert [a["headline"] for a in assembled["must_know"]] == ["keep me"]


class TestNotCoveredBlurb:
    """not_covered_blurb is a footer garnish copied from selected.json -- a
    missing/empty/malformed source must never break assembly (see merge.py
    ``_load_not_covered_blurb``)."""

    def test_copies_blurb_when_present(self, tmp_path):
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"must_know": [], "should_know": [], "not_covered_blurb": "Skipped celebrity news."},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["not_covered_blurb"] == "Skipped celebrity news."

    def test_absent_when_selected_json_missing(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("h")])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))  # no selected.json

        with caplog.at_level("INFO", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "not_covered_blurb" not in assembled
        assert any("not_covered_blurb" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "selected",
        [{"must_know": [], "should_know": []}, {"not_covered_blurb": "   "}],
        ids=["field-missing", "blank-string"],
    )
    def test_absent_when_field_missing_or_blank(self, tmp_path, selected):
        draft = _draft(must_know=[_article("h")])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}), selected=selected)

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "not_covered_blurb" not in assembled

    def test_absent_when_field_wrong_type(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": ["not", "a", "string"]},
        )

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "not_covered_blurb" not in assembled
        # Wrong-typed-but-present must be loud (WARNING), not conflated with
        # the benign absent/empty case -- it means SELECT emitted something
        # off-schema, which is worth knowing about in prod (root logger INFO).
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("not_covered_blurb" in r.getMessage() and "list" in r.getMessage() for r in warnings)

    def test_absent_when_selected_json_malformed(self, tmp_path, caplog):
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected="{ not valid json",
        )

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())  # must not raise

        assert "not_covered_blurb" not in assembled
        # An actual read/parse failure (vs. the routine missing-file case)
        # matches _load_cluster_map's severity for the same condition.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("selected.json unreadable" in r.getMessage() for r in warnings)

    def test_absent_when_selected_json_not_utf8(self, tmp_path, caplog):
        # UnicodeDecodeError is a ValueError subclass, same as
        # json.JSONDecodeError -- must be caught, not propagate and abort
        # the whole run (the docstring's never-raises contract).
        draft = _draft(must_know=[_article("h")])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))
        (tmp_path / "selected.json").write_bytes(b"\xff\xfe not utf-8 at all")

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())  # must not raise

        assert "not_covered_blurb" not in assembled
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("selected.json unreadable" in r.getMessage() for r in warnings)

    def test_overlong_blurb_is_truncated_not_dropped(self, tmp_path):
        # Never let a garnish break schema validation: truncate to the schema
        # cap instead of failing assembly.
        long_blurb = "x" * 400
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": long_blurb},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert len(assembled["not_covered_blurb"]) <= 300


class TestMalformedIntermediateFiles:
    """Truncated/invalid intermediate JSON must surface as the documented
    RuntimeError contract, not a bare JSONDecodeError -- this path is hit by
    --write-only, where no upstream validator re-parses these files first."""

    def test_malformed_coherence_raises_runtime_error(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft(must_know=[_article("a")])))
        (tmp_path / "coherence_report.json").write_text('{"results": [trunca')  # invalid JSON

        with pytest.raises(RuntimeError):
            assemble_selections(tmp_path)

    def test_malformed_draft_raises_runtime_error(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text("{not json")
        (tmp_path / "coherence_report.json").write_text(json.dumps(_coherence()))

        with pytest.raises(RuntimeError):
            assemble_selections(tmp_path)
