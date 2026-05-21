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


def _signal(headline, article_id="A1"):
    return {"headline": headline, "source": {"article_id": article_id}}


def _draft(must_know=None, should_know=None, signals=None, preheader="Preheader."):
    return {
        "must_know": must_know or [],
        "should_know": should_know or [],
        "signals": signals or {"americas": [], "europe": [], "asia_pacific": [], "middle_east_africa": [], "tech": []},
        "preheader": preheader,
    }


def _coherence(*results):
    return {"results": list(results)}


def _write(tmp_path, draft, coherence):
    (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
    (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))


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

    def test_drops_failed_signals_per_region(self, tmp_path):
        draft = _draft(
            must_know=[_article("mk")],
            signals={
                "americas": [_signal("ok"), _signal("nope")],
                "europe": [_signal("eu-ok")],
                "asia_pacific": [],
                "middle_east_africa": [],
                "tech": [_signal("tech-bad")],
            },
        )
        coherence = _coherence(
            {"headline": "mk", "pass": True},
            {"headline": "ok", "pass": True},
            {"headline": "nope", "pass": False},
            {"headline": "eu-ok", "pass": True},
            {"headline": "tech-bad", "pass": False},
        )
        _write(tmp_path, draft, coherence)

        out = assemble_selections(tmp_path)
        assembled = json.loads(out.read_text())

        assert [s["headline"] for s in assembled["signals"]["americas"]] == ["ok"]
        assert [s["headline"] for s in assembled["signals"]["europe"]] == ["eu-ok"]
        assert assembled["signals"]["tech"] == []

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
        bad = {"must_know": [], "should_know": []}
        errors = validate_selections(bad)
        assert any("signals" in e for e in errors)
        assert any("preheader" in e for e in errors)

    def test_rejects_old_source_schema(self):
        bad = self._valid()
        bad["must_know"][0]["sources"] = [{"name": "BBC", "url": "https://bbc.com", "bias": "center"}]
        errors = validate_selections(bad)
        assert errors

    def test_rejects_extra_signal_keys(self):
        bad = self._valid()
        bad["signals"]["americas"] = [{"headline": "x", "source": {"article_id": "A1"}, "extra": "no"}]
        errors = validate_selections(bad)
        assert errors
