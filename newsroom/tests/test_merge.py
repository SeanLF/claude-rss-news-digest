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


def _write(tmp_path, draft, coherence, clusters=None):
    (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
    (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))
    if clusters is not None:
        (tmp_path / "clusters.json").write_text(json.dumps(clusters))


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

    def test_pass_missing_defaults_to_keep(self, tmp_path):
        # If coherence omits a headline entirely, treat it as passing.
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
