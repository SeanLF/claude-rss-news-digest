"""Tests for merge.py (post-dispatcher selections assembly) and schema validation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_graders import GraderLimits
from merge import assemble_selections
from schema import NOT_COVERED_BLURB_MAX_LEN, SELECTIONS_SCHEMA, validate_selections

# Every top-level string field in the schema that carries a length cap. Derived
# from the schema so a newly-added capped field is picked up automatically.
_CAPPED_STRING_FIELDS = sorted(
    (name, spec["maxLength"])
    for name, spec in SELECTIONS_SCHEMA["properties"].items()
    if spec.get("type") == "string" and "maxLength" in spec
)


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

    def test_small_preheader_overshoot_ships_untouched(self, tmp_path, caplog):
        # A cosmetic 2-char overshoot on the inbox-preview preheader (WRITE
        # routinely nudges past its 150 target) must NOT abort a delivered
        # digest, and must NOT be mangled -- chopping a clause reads worse than
        # a couple extra chars. This is the exact string that killed run 229.
        preheader = (
            "US-Iran ceasefire collapses amid assassination threats and Hormuz "
            "standoff; Spain wildfire kills 12; Russia strikes Kyiv as Zaporizhzhia "
            "front tightens."
        )
        assert 150 < len(preheader) <= 157  # within the 5% tolerance band
        draft = _draft(must_know=[_article("h")], preheader=preheader)
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        out = assemble_selections(tmp_path)

        result = json.loads(out.read_text())
        assert result["preheader"] == preheader  # untouched, not truncated
        assert validate_selections(result) == []
        assert not any("preheader exceeds" in r.message for r in caplog.records)

    def test_gross_preheader_overshoot_truncates_not_fails(self, tmp_path, caplog):
        # A gross overshoot signals a WRITE malfunction, not a nudge -- degrade
        # gracefully (word-boundary truncate) rather than abort the digest.
        preheader = "Breaking: " + "everything happened at once and then some more. " * 5
        assert len(preheader) > 157
        draft = _draft(must_know=[_article("h")], preheader=preheader)
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        out = assemble_selections(tmp_path)

        result = json.loads(out.read_text())
        assert len(result["preheader"]) <= 157
        assert result["preheader"].endswith("…")
        assert "…" not in result["preheader"][:-1]  # single trailing ellipsis, whole words
        assert validate_selections(result) == []
        assert any("preheader exceeds" in r.message for r in caplog.records)

    @pytest.mark.parametrize("field,cap", _CAPPED_STRING_FIELDS)
    def test_capped_field_never_hard_aborts_assembly(self, field, cap, tmp_path):
        # Structural invariant: NO length-capped top-level string field may
        # hard-abort assembly -- it degrades (word-boundary truncate) before
        # validation. Parametrized off the schema, so a newly-added capped field is
        # covered here automatically and this fails until it too degrades. (Run 229,
        # 2026-07-11: an un-degraded preheader cap cost a delivered digest.)
        draft = _draft(must_know=[_article("h")])
        draft[field] = "overflow " * cap  # ~9x the cap, comfortably over
        assert len(draft[field]) > cap
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        out = assemble_selections(tmp_path)  # must NOT raise

        result = json.loads(out.read_text())
        assert len(result[field]) <= cap
        assert validate_selections(result) == []


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

    def test_blanking_emits_an_aggregate_rate_line(self, tmp_path, caplog):
        """Blanking is the one degradation that SHIPS -- nothing is dropped, so no other
        health signal fires. The rate must be reported as ONE aggregate line, not only as N
        per-story warnings that each read like a one-off."""
        draft = _draft(must_know=[_article("good"), _article("bad1"), _article("bad2")])
        coherence = _coherence(
            {"headline": "bad1", "pass": False, "reason": "why: unsupported", "failed_fields": ["why_it_matters"]},
            {"headline": "bad2", "pass": False, "reason": "why: unsupported", "failed_fields": ["why_it_matters"]},
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert len(assembled["must_know"]) == 3  # nothing dropped -- that is the point
        aggregate = [r.getMessage() for r in caplog.records if "blanked why_it_matters on" in r.getMessage()]
        assert len(aggregate) == 1, f"expected exactly one aggregate line, got: {aggregate!r}"
        assert "2 of 3 assembled stories" in aggregate[0]
        assert "67%" in aggregate[0]

    def test_aggregate_line_names_why_repair_did_not_cover_it(self, tmp_path, caplog):
        """The line is read before the query is. It must not assert a cause it cannot know:
        with no repair_resolution.json on disk, "repair produced no usable patch" would send
        an operator hunting the repairer when repair wrote nothing to consume."""
        draft = _draft(must_know=[_article("good"), _article("bad1")])
        coherence = _coherence(
            {"headline": "bad1", "pass": False, "reason": "why: unsupported", "failed_fields": ["why_it_matters"]},
        )
        _write(tmp_path, draft, coherence)
        assert not (tmp_path / "repair_resolution.json").exists()

        with caplog.at_level("WARNING"):
            assemble_selections(tmp_path)
        line = next(r.getMessage() for r in caplog.records if "blanked why_it_matters on" in r.getMessage())
        assert "no repair resolution this run" in line
        assert "no usable patch" not in line

    def test_no_aggregate_line_when_nothing_is_blanked(self, tmp_path, caplog):
        """The aggregate must not fire on a clean run -- a line that always
        appears carries no signal."""
        draft = _draft(must_know=[_article("good")])
        _write(tmp_path, draft, _coherence())

        with caplog.at_level("WARNING"):
            assemble_selections(tmp_path)

        assert not any("blanked why_it_matters on" in r.getMessage() for r in caplog.records)

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
        # Derived from the cap, not restated: hardcoding a word count silently stops
        # exercising the boundary the moment the cap moves.
        long_summary = "word " * (GraderLimits().summary_max_words + 10)
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

    def test_a_flattened_why_it_matters_reaches_the_run_log(self, tmp_path, caplog):
        """The only per-run visibility this check has. db.get_run_health builds the health
        dict in one statement, run_artifacts among the tables it reads -- run_health.py itself
        is predicates over that dict -- and containment is not expressible in SQL, so merge's
        warning is where an operator sees it."""
        item = _article("h")
        item["summary"] = "Regulators approved the merger after a lengthy antitrust investigation."
        item["why_it_matters"] = (
            "It matters because regulators approved the merger after a lengthy antitrust investigation."
        )
        draft = _draft(must_know=[item])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assemble_selections(tmp_path)

        assert any("why_it_matters_restates_summary" in r.getMessage() for r in caplog.records)

    def test_a_two_sentence_why_it_matters_reaches_the_run_log(self, tmp_path, caplog):
        item = _article("h")
        item["why_it_matters"] = "The ruling clears the merger. It does not settle pricing."
        draft = _draft(must_know=[item])
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assemble_selections(tmp_path)

        assert any("why_it_matters_sentence_count" in r.getMessage() for r in caplog.records)

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
        bad["not_covered_blurb"] = "x" * (NOT_COVERED_BLURB_MAX_LEN + 1)
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

    def test_blurb_at_observed_production_length_is_not_truncated(self, tmp_path):
        # The cap exists to bound a reader-facing footer, not to clip it mid-clause.
        # Measured over 26 production digests (2026-07-02 onwards): SELECT writes
        # 303-463 chars and 62% of digests shipped a footer ending in an ellipsis.
        # The longest real blurb observed must survive verbatim.
        real_blurb = "We left out " + "several genuine in-scope stories today, " * 11 + "and others."
        assert len(real_blurb) == 463  # the longest blurb observed in production
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": real_blurb},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["not_covered_blurb"] == real_blurb.strip()
        assert not assembled["not_covered_blurb"].endswith("…")

    def test_overlong_blurb_is_truncated_not_dropped(self, tmp_path):
        # Never let a garnish break schema validation: truncate to the schema
        # cap instead of failing assembly.
        long_blurb = "x" * (NOT_COVERED_BLURB_MAX_LEN + 100)
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": long_blurb},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert len(assembled["not_covered_blurb"]) <= NOT_COVERED_BLURB_MAX_LEN

    @pytest.mark.parametrize(
        "leaky_blurb",
        [
            "Skipped sports (clusters 0, 1, and World Cup articles) and the heatwave (cluster 132).",
            "Filtered US domestic lifestyle: cluster 46 and cluster 89.",
            "Dropped a minor thread referenced as [A221] in the source set.",
            # Parenthesised ids are the form WRITE actually used in run 247
            # (2026-07-28); the guard matched only the bracketed form until then,
            # so the same leak class had a live hole on the footer path too.
            "Dropped a minor thread referenced as (A221) in the source set.",
            "Skipped duplicate wire copy (A110, A263).",
        ],
        ids=["cluster-list", "single-clusters", "article-id", "article-id-paren", "article-id-paren-multi"],
    )
    def test_drops_blurb_leaking_internal_ids(self, tmp_path, caplog, leaky_blurb):
        # The footer is reader-facing. SELECT's internal cluster/article
        # indices ("cluster 132", "[A221]") must never reach a reader -- if the
        # blurb still carries them, drop it (degrade to no footer) and warn,
        # rather than sanitising freeform prose or shipping the leak.
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": leaky_blurb},
        )

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "not_covered_blurb" not in assembled
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("not_covered_blurb" in r.getMessage() and "internal" in r.getMessage().lower() for r in warnings)

    def test_clean_blurb_survives(self, tmp_path):
        # A plain reader-facing sentence with no internal markers passes through
        # untouched -- the leak guard must not be trigger-happy on ordinary prose
        # that merely contains numbers.
        blurb = "We left out sports, celebrity news, and coverage of the 12 World Cup group matches."
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": blurb},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["not_covered_blurb"] == blurb

    def test_overlong_blurb_truncates_on_word_boundary(self, tmp_path):
        # A legit-but-long blurb must degrade to a clean truncation, never a
        # mid-word cut like the "...SO…" that shipped on 2026-07-02.
        long_blurb = "We deliberately skipped several softer stories " * 20  # over the cap
        assert len(long_blurb) > NOT_COVERED_BLURB_MAX_LEN
        draft = _draft(must_know=[_article("h")])
        _write(
            tmp_path,
            draft,
            _coherence({"headline": "h", "pass": True}),
            selected={"not_covered_blurb": long_blurb},
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())
        blurb = assembled["not_covered_blurb"]

        assert len(blurb) <= NOT_COVERED_BLURB_MAX_LEN
        assert blurb.endswith("…")
        # The character before the ellipsis must be a word char, and the word it
        # ends on must be whole -- i.e. the truncation happened at a space, so
        # stripping the ellipsis leaves text that is a prefix ending on a full word.
        assert blurb[:-1] == blurb[:-1].rstrip()
        assert long_blurb.startswith(blurb[:-1].rstrip())
        assert blurb[:-1].rstrip().split()[-1] in long_blurb.split()


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


class TestReportingVariesIdLeak:
    """reporting_varies is reader-facing (rendered in both the web digest and the
    email), but nothing downstream ever checked it for internal article IDs.

    Run 247 (2026-07-28) shipped ``<b>NYT (A316):</b>`` and
    ``<b>Daily Maverick, Le Monde, Rappler, Reuters (A110, A263, A349, A358):</b>``
    to 11 subscribers. write.md tells WRITE these are "NOT article references",
    but a prompt instruction is not enforcement -- and the existing
    ``_INTERNAL_ID_PATTERNS`` guard only ran on not_covered_blurb, matching the
    bracketed ``[A221]`` form, so it would have missed the parenthesised form
    even if it had been wired up here.

    The policy is STRIP, on every field, never drop the entry. An id group is a
    self-contained parenthetical, so removing it leaves readable text behind --
    and when the guard misfires on a real "(A320)" or "(A7)", a lost
    parenthetical beats a lost comparison.
    """

    @staticmethod
    def _with_varies(*entries):
        item = _article("h")
        item["reporting_varies"] = list(entries)
        return _draft(must_know=[item])

    @pytest.mark.parametrize(
        ("leaky_source", "expected"),
        [
            ("NYT (A316)", "NYT"),
            (
                "Daily Maverick, Le Monde, Rappler, Reuters (A110, A263, A349, A358)",
                "Daily Maverick, Le Monde, Rappler, Reuters",
            ),
            ("Reuters / AFP (A30, A32)", "Reuters / AFP"),
            ("NYT [A221]", "NYT"),  # legacy bracketed form
            # A model writing a list of four ends the run with "and" as often as
            # with a comma; a separator-blind guard is the same hole as a
            # delimiter-blind one.
            ("NYT (A316, A317 and A318)", "NYT"),
            ("NYT (A110; A263)", "NYT"),
        ],
        ids=["single-paren", "multi-id-paren", "slash-names", "bracketed", "and-join", "semicolon-join"],
    )
    def test_strips_id_group_from_source_and_keeps_entry(self, tmp_path, caplog, leaky_source, expected):
        draft = self._with_varies({"source": leaky_source, "angle": "Reports 6.8.", "bias": "center"})
        _write(tmp_path, draft, _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        varies = assembled["must_know"][0]["reporting_varies"]
        assert [v["source"] for v in varies] == [expected]
        assert varies[0]["angle"] == "Reports 6.8."  # untouched
        assert any("reporting_varies" in r.getMessage() for r in caplog.records)

    def test_leaves_clean_entry_untouched_and_silent(self, tmp_path, caplog):
        clean = {"source": "Reuters / AFP", "angle": "Frame the visit as pivotal.", "bias": "center"}
        _write(tmp_path, self._with_varies(clean), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["reporting_varies"] == [clean]
        assert not any("reporting_varies" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        ("leaky_entry", "expected"),
        [
            (
                {"source": "NYT", "angle": "Reports the quake as 6.8 (A316), a preliminary reading.", "bias": "c"},
                {"source": "NYT", "angle": "Reports the quake as 6.8, a preliminary reading.", "bias": "c"},
            ),
            (
                {"source": "NYT", "angle": "Reports 6.8.", "bias": "center [A316]"},
                {"source": "NYT", "angle": "Reports 6.8.", "bias": "center"},
            ),
        ],
        ids=["angle-prose", "bias"],
    )
    def test_strips_leak_from_prose_fields_and_keeps_the_entry(self, tmp_path, caplog, leaky_entry, expected):
        # Dropping the entry would cost the reader a whole comparison over a
        # parenthetical -- and would do so on a false positive too.
        _write(tmp_path, self._with_varies(leaky_entry), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["reporting_varies"] == [expected]
        assert any("reporting_varies" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "field_value",
        [
            "Frames it as a cluster 40 cases wide, not a national outbreak.",
            "Reports the toll (at least 40) as provisional.",
            "Notes the vote split 6-3 (Roberts concurring).",
        ],
        ids=["cluster-n-prose", "plain-parenthetical", "parenthetical-with-name"],
    )
    def test_preserves_legitimate_parentheses_and_cluster_prose(self, tmp_path, caplog, field_value):
        # The guard edits reader-facing text, so its false-positive boundary is
        # the half worth pinning. "cluster 40 cases" is ordinary news prose --
        # the SELECT-scoped cluster pattern must not reach WRITE's fields at all.
        entry = {"source": "Reuters", "angle": field_value, "bias": "center"}
        _write(tmp_path, self._with_varies(entry), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["reporting_varies"] == [entry]
        assert not any("reporting_varies" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        ("field_value", "expected"),
        [
            (
                "Focuses on the aircraft type (A320) rather than the carrier.",
                "Focuses on the aircraft type rather than the carrier.",
            ),
            ("Places the crash on the motorway (A7) near Lyon.", "Places the crash on the motorway near Lyon."),
        ],
        ids=["airbus", "motorway"],
    )
    def test_known_collateral_a_designators_are_stripped(self, tmp_path, field_value, expected):
        # KNOWN AND ACCEPTED: "(A320)" is an Airbus model and "(A7)" a French
        # motorway. Neither is distinguishable from an article id by any lexical
        # rule -- article ids run A1..A{n} with n in the hundreds, so the ranges
        # genuinely overlap. This test exists so that when someone finds an
        # aircraft model missing from a digest, one grep lands here instead of a
        # log dive. Stripping is why this is survivable: the sentence still
        # reads, which a dropped entry would not.
        entry = {"source": "Reuters", "angle": field_value, "bias": "center"}
        _write(tmp_path, self._with_varies(entry), _coherence({"headline": "h", "pass": True}))

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["reporting_varies"][0]["angle"] == expected

    def test_scrubs_should_know_tier_too(self, tmp_path):
        brief = _article("brief")
        brief["reporting_varies"] = [{"source": "NYT (A316)", "angle": "Reports 6.8.", "bias": "center"}]
        _write(
            tmp_path,
            _draft(must_know=[_article("h")], should_know=[brief]),
            _coherence({"headline": "h", "pass": True}, {"headline": "brief", "pass": True}),
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["should_know"][0]["reporting_varies"][0]["source"] == "NYT"

    @pytest.mark.parametrize(
        "entry",
        [
            {"source": "(A316)", "angle": "Reports 6.8.", "bias": "center"},
            {"source": "NYT", "angle": "[A316]", "bias": "center"},
        ],
        ids=["source-was-only-an-id", "angle-was-only-an-id"],
    )
    def test_drops_entry_left_empty_by_scrubbing(self, tmp_path, entry):
        # Nothing readable survives, and "NYT:" with no angle is a dangling row.
        _write(tmp_path, self._with_varies(entry), _coherence({"headline": "h", "pass": True}))

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        # Schema allows the key to be absent; an empty array would render a
        # dangling "How reporting varies" label with no content.
        assert "reporting_varies" not in assembled["must_know"][0]

    def test_drops_non_dict_entry_with_a_warning(self, tmp_path, caplog):
        clean = {"source": "Reuters", "angle": "Reports 7.1.", "bias": "center"}
        _write(tmp_path, self._with_varies("not an object", clean), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["reporting_varies"] == [clean]
        assert any("not an object" in r.getMessage() for r in caplog.records)

    def test_warns_rather_than_declining_silently_on_a_non_list(self, tmp_path, caplog):
        # Schema validation rejects this shape and aborts the run; without a log
        # line here the abort gives no hint the guard was ever involved.
        item = _article("h")
        item["reporting_varies"] = {"source": "NYT", "angle": "a", "bias": "b"}  # dict, not list
        _write(tmp_path, _draft(must_know=[item]), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"), pytest.raises(RuntimeError):
            assemble_selections(tmp_path)

        assert any("not a list" in r.getMessage() for r in caplog.records)

    def test_names_the_story_in_every_warning(self, tmp_path, caplog):
        # A bare warning cannot be traced back to a story once a digest has 16.
        item = _article("Quake hits northern Japan")
        item["reporting_varies"] = [{"source": "NYT (A316)", "angle": "Reports 6.8.", "bias": "center"}]
        _write(tmp_path, _draft(must_know=[item]), _coherence({"headline": "Quake hits northern Japan", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assemble_selections(tmp_path)

        rv = [r.getMessage() for r in caplog.records if "reporting_varies" in r.getMessage()]
        assert rv and all("Quake hits northern Japan" in m for m in rv if "entries scrubbed" not in m)

    def test_logs_one_aggregate_tally(self, tmp_path, caplog):
        # N scattered warnings each read as a one-off; the tally reads as a
        # WRITE regression, which is what a broad misfire actually is.
        leaky = [{"source": f"NYT (A{i})", "angle": "Reports 6.8.", "bias": "center"} for i in (1, 2, 3)]
        _write(tmp_path, self._with_varies(*leaky), _coherence({"headline": "h", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assemble_selections(tmp_path)

        assert any("3 entries scrubbed of internal ids, 0 dropped" in r.getMessage() for r in caplog.records)


class TestWriteFieldIdLeakIsObservedNotEdited:
    """headline/summary/why_it_matters/preheader are WRITE-authored and render
    verbatim (render.py, render_email.py), exactly like reporting_varies -- but
    unlike reporting_varies they are deliberately NOT scrubbed.

    Run 247 proved WRITE will put "(A316)" in a field its prompt forbids it in, so
    the risk is real. The response is asymmetric on purpose: the id pattern cannot
    be told apart from a real "(A320)" Airbus or "(A7)" motorway, and eating a
    parenthetical out of a source NAME is survivable where silently rewriting a
    headline is not. Zero leaks in these four fields across all 30 published
    digests, so this WATCHES them -- the L1 graders already run non-fatally on the
    assembled payload, so the next occurrence lands in the run log as evidence
    instead of in a subscriber's inbox unnoticed.
    """

    def test_leak_in_summary_is_warned_but_text_ships_untouched(self, tmp_path, caplog):
        leaky = "The quake measured 6.8 (A316) by early estimates."
        item = _article("Quake hits northern Japan")
        item["summary"] = leaky
        _write(tmp_path, _draft(must_know=[item]), _coherence({"headline": "Quake hits northern Japan", "pass": True}))

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        # The whole point: observed, never edited. Byte-identical to WRITE's output.
        assert assembled["must_know"][0]["summary"] == leaky
        messages = [r.getMessage() for r in caplog.records]
        assert any("no_internal_article_ids" in m for m in messages)
        assert any("(A316)" in m for m in messages), "the log must quote the match, or it is not evidence"

    def test_leak_in_headline_is_warned(self, tmp_path, caplog):
        item = _article("Quake hits northern Japan (A316)")
        _write(
            tmp_path,
            _draft(must_know=[item]),
            _coherence({"headline": "Quake hits northern Japan (A316)", "pass": True}),
        )

        with caplog.at_level("WARNING", logger="merge"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["headline"] == "Quake hits northern Japan (A316)"
        assert any("no_internal_article_ids" in r.getMessage() for r in caplog.records)

    def test_clean_run_says_nothing_about_id_leaks(self, tmp_path, caplog):
        # A guard that cries on every clean run trains people to ignore it.
        draft = _draft(
            must_know=[_article("Quake hits northern Japan")],
            should_know=[_article(f"Brief {i}", article_id=f"A{i + 2}") for i in range(3)],
        )
        coherence = _coherence(
            {"headline": "Quake hits northern Japan", "pass": True},
            *({"headline": f"Brief {i}", "pass": True} for i in range(3)),
        )
        _write(tmp_path, draft, coherence)

        with caplog.at_level("WARNING", logger="merge"):
            assemble_selections(tmp_path)

        assert not any("no_internal_article_ids" in r.getMessage() for r in caplog.records)


class TestClusterAttributionUsesPlurality:
    """cluster_id is the join key for thread context, and it was assigned by whichever source
    happened to be listed first.

    Run 247, verified against the archived artifacts: the must_know story "Netanyahu and
    Zelensky meet Trump separately..." cited 18 articles -- 16 in cluster 62, 2 in cluster 16 --
    and first-source handed it to the 2-article minority. A should_know story also resolved to
    16, so both were flagged as sharing a cluster_id and BOTH silently lost their thread delta.

    Plurality sends the must_know story to 62 where 16 of its 18 sources live, which both fixes
    the attribution and dissolves the collision.
    """

    @staticmethod
    def _clusters(mapping):
        return {"clusters": [{"story": story, "article_ids": ids} for story, ids in mapping]}

    def test_majority_cluster_wins_over_the_first_listed_source(self, tmp_path):
        item = _article("h")
        # Two sources in the small cluster, three in the big one; the small one is listed first.
        item["sources"] = [{"article_id": a} for a in ("A30", "A32", "A79", "A82", "A177")]
        _write(
            tmp_path,
            _draft(must_know=[item]),
            _coherence({"headline": "h", "pass": True}),
            clusters=self._clusters(
                [("small cluster", ["A18", "A30", "A32"]), ("big cluster", ["A79", "A82", "A177"])]
            ),
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["cluster_id"] == "big cluster"

    def test_two_stories_drawing_on_one_cluster_no_longer_collide(self, tmp_path):
        # The run-247 shape: both stories touch the small cluster, but only one belongs to it.
        mostly_big = _article("mostly big")
        mostly_big["sources"] = [{"article_id": a} for a in ("A30", "A32", "A79", "A82", "A177")]
        truly_small = _article("truly small")
        truly_small["sources"] = [{"article_id": a} for a in ("A18", "A30")]
        _write(
            tmp_path,
            _draft(must_know=[mostly_big, truly_small]),
            _coherence({"headline": "mostly big", "pass": True}, {"headline": "truly small", "pass": True}),
            clusters=self._clusters(
                [("small cluster", ["A18", "A30", "A32"]), ("big cluster", ["A79", "A82", "A177"])]
            ),
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        ids = [a["cluster_id"] for a in assembled["must_know"]]
        assert len(set(ids)) == 2, f"both stories still share a cluster_id: {ids}"

    def test_a_tie_keeps_the_earliest_cited_cluster(self, tmp_path):
        # Ties are real (run 247's should_know split 2-2). Any rule is fine; a non-deterministic
        # one is not, since cluster_id is a join key. Asserted as a VALUE, not by looping in one
        # process -- an in-process loop cannot vary and so cannot detect non-determinism.
        item = _article("h")
        item["sources"] = [{"article_id": a} for a in ("A18", "A30", "A71", "A677")]
        _write(
            tmp_path,
            _draft(must_know=[item]),
            _coherence({"headline": "h", "pass": True}),
            clusters=self._clusters([("first cluster", ["A18", "A30"]), ("second cluster", ["A71", "A677"])]),
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["must_know"][0]["cluster_id"] == "first cluster"

    def test_unmapped_sources_still_leave_cluster_id_absent(self, tmp_path):
        item = _article("h")
        item["sources"] = [{"article_id": "A999"}]
        _write(
            tmp_path,
            _draft(must_know=[item]),
            _coherence({"headline": "h", "pass": True}),
            clusters=self._clusters([("some cluster", ["A1", "A2"])]),
        )

        assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert "cluster_id" not in assembled["must_know"][0]

    def test_repeated_citations_do_not_out_vote_distinct_articles(self):
        # sources is an evidence-citation list (write.md tells WRITE to ADD any article that
        # supports a specific), so the same article can be cited repeatedly. Counting those as
        # separate votes lets one article outvote three. _item_article_ids treats sources as a
        # set twenty lines up in this module; the vote must agree.
        from merge import _attach_cluster_id

        item: dict = {}
        cluster_map = {"A1": "own", "A2": "own", "A3": "own", "A9": "other"}
        sources = [{"article_id": a} for a in ("A1", "A2", "A3", "A9", "A9", "A9", "A9")]
        _attach_cluster_id(item, sources, cluster_map)

        assert item["cluster_id"] == "own"

    def test_non_string_article_id_does_not_abort_the_run(self):
        # An unhashable article_id would raise out of assemble_selections and lose the digest.
        from merge import _attach_cluster_id

        item: dict = {}
        _attach_cluster_id(item, [{"article_id": ["A30"]}, {"article_id": "A1"}], {"A1": "own"})

        assert item["cluster_id"] == "own"


class TestPreheaderNeverBlocksDelivery:
    """Doctrine since run 229, where a 152-char preheader against a hard 150 cap aborted a
    run: every preheader failure mode degrades. The cap truncates (tested above with the
    other capped fields); an absent or blank value substitutes the top must_know headline.
    Both paths reach here -- one WRITE call for every story, or one per story with a
    separate preheader stage that can fail on its own."""

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_a_blank_preheader_is_filled_from_the_top_headline(self, tmp_path, value, caplog):
        draft = _draft(must_know=[_article("Iran strikes widen"), _article("Quake toll rises", "A2")])
        if value is None:
            del draft["preheader"]
        else:
            draft["preheader"] = value
        _write(tmp_path, draft, _coherence())

        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())

        assert assembled["preheader"] == "Iran strikes widen"
        assert "preheader is empty" in caplog.text

    def test_the_fill_skips_a_blank_headline(self, tmp_path):
        """SELECTIONS_SCHEMA permits an empty headline string, so must_know[0] is not
        guaranteed to carry text -- substituting one blank for another would look like it
        worked."""
        draft = _draft(
            must_know=[_article(""), _article("Quake toll rises", "A2")],
            should_know=[_article("Something else", "A3")],
            preheader="",
        )
        _write(tmp_path, draft, _coherence())
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert assembled["preheader"] == "Quake toll rises"

    def test_the_fill_reaches_should_know_when_every_must_know_headline_is_blank(self, tmp_path):
        draft = _draft(
            must_know=[_article("")],
            should_know=[_article("Quake toll rises", "A2")],
            preheader="",
        )
        _write(tmp_path, draft, _coherence())
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert assembled["preheader"] == "Quake toll rises"

    def test_a_real_preheader_is_left_alone(self, tmp_path):
        draft = _draft(must_know=[_article("Iran strikes widen")], preheader="Two things happened today.")
        _write(tmp_path, draft, _coherence())
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert assembled["preheader"] == "Two things happened today."

    def test_the_substituted_headline_is_still_capped(self, tmp_path):
        long_headline = "word " * 60
        draft = _draft(must_know=[_article(long_headline.strip())], preheader="")
        _write(tmp_path, draft, _coherence())
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert 0 < len(assembled["preheader"]) <= SELECTIONS_SCHEMA["properties"]["preheader"]["maxLength"]


class TestShouldKnowCarriesNoWhyItMatters:
    """Briefs render headline + summary only, so the field is a must_know field: assembly strips
    it from should_know (a draft written before the change, or a WRITE that ignored the
    prompt) and the schema stops requiring it there."""

    def test_assembly_strips_why_it_matters_from_should_know(self, tmp_path):
        draft = _draft(must_know=[_article("a")], should_know=[_article("b", "A2")])
        _write(tmp_path, draft, _coherence())
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert "why_it_matters" not in assembled["should_know"][0]
        assert assembled["must_know"][0]["why_it_matters"] == "Why."

    def test_schema_accepts_should_know_without_it_and_still_requires_it_for_must_know(self):
        sel = {"must_know": [_article("a")], "should_know": [_article("b", "A2")], "preheader": "p"}
        del sel["should_know"][0]["why_it_matters"]
        assert validate_selections(sel) == []
        del sel["must_know"][0]["why_it_matters"]
        assert any("why_it_matters" in e for e in validate_selections(sel))

    def test_a_why_only_flag_on_should_know_is_moot(self, tmp_path, caplog):
        """The flagged field never ships, so there is nothing to blank and nothing to count:
        the story is kept as-is and the blanking rate stays a must_know rate."""
        draft = _draft(must_know=[_article("a")], should_know=[_article("b", "A2")])
        coherence = _coherence(
            {"headline": "b", "pass": False, "reason": "why_it_matters: uncited", "failed_fields": ["why_it_matters"]}
        )
        _write(tmp_path, draft, coherence)
        with caplog.at_level("WARNING"):
            assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert [a["headline"] for a in assembled["should_know"]] == ["b"]
        assert "why_it_matters" not in assembled["should_know"][0]
        assert not any("why_it_matters" in r.getMessage() for r in caplog.records)

    def test_a_repair_patch_cannot_put_why_it_matters_back_on_should_know(self, tmp_path):
        """--resume with a stale coherence report and resolution from before the change:
        the patch names why_it_matters on a should_know story. The field still does not ship."""
        draft = _draft(must_know=[_article("a")], should_know=[_article("b", "A2")])
        coherence = _coherence(
            {"headline": "b", "pass": False, "reason": "why_it_matters: uncited", "failed_fields": ["why_it_matters"]}
        )
        _write(tmp_path, draft, coherence)
        (tmp_path / "repair_resolution.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "article_ids": ["A2"],
                            "status": "repaired",
                            "recheck_pass": True,
                            "patched_fields": {"why_it_matters": "Patched why."},
                        }
                    ]
                }
            )
        )
        assembled = json.loads(assemble_selections(tmp_path).read_text())
        assert [a["headline"] for a in assembled["should_know"]] == ["b"]
        assert "why_it_matters" not in assembled["should_know"][0]
