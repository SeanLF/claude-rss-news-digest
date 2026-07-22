"""Tests for repair.py -- the deterministic core of repair-not-drop.

When COHERENCE fails a story's headline or summary, merge.py currently drops the
WHOLE story (only a why_it_matters-only failure degrades gracefully). On a real
archived day (run 245) that dropped a must_know lead over one bad clause. Repair
regenerates just the flagged field and re-checks it; this module is the pure,
model-free scaffolding (request builder, guarded patch application, resolution
assembly, event log) that Step 2 wires a model into. Every path here is
deterministic and TDD-covered, including every fail-closed fallback to a drop.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import repair


def _item(headline, summary="A summary.", why="A why.", article_ids=("A1",)):
    return {
        "headline": headline,
        "summary": summary,
        "why_it_matters": why,
        "sources": [{"article_id": a} for a in article_ids],
    }


def _fail(headline, failed_fields, reason="field: bad", article_ids=("A1",)):
    return {
        "headline": headline,
        "article_ids": list(article_ids),
        "pass": False,
        "reason": reason,
        "failed_fields": failed_fields,
    }


def _draft(must_know=None, should_know=None):
    return {"must_know": must_know or [], "should_know": should_know or [], "preheader": "p"}


class TestBuildRepairRequests:
    def test_builds_request_for_headline_failure(self):
        draft = _draft(must_know=[_item("H", article_ids=("A5",))])
        coherence = {"results": [_fail("H", ["headline"], "headline: wrong entity", ("A5",))]}

        out = repair.build_repair_requests(draft, coherence)

        assert len(out["requests"]) == 1
        req = out["requests"][0]
        assert req["failed_fields"] == ["headline"]

    def test_builds_request_for_summary_failure(self):
        draft = _draft(should_know=[_item("H", article_ids=("A9",))])
        coherence = {"results": [_fail("H", ["summary"], "summary: uncited duration", ("A9",))]}

        out = repair.build_repair_requests(draft, coherence)

        assert [r["failed_fields"] for r in out["requests"]] == [["summary"]]

    def test_builds_request_for_both_headline_and_summary(self):
        draft = _draft(must_know=[_item("H", article_ids=("A2",))])
        coherence = {"results": [_fail("H", ["headline", "summary"], "both bad", ("A2",))]}

        out = repair.build_repair_requests(draft, coherence)

        assert set(out["requests"][0]["failed_fields"]) == {"headline", "summary"}

    def test_skips_why_it_matters_only_failure(self):
        # why-only stays on merge.py's existing blank path -- not a repair request.
        draft = _draft(must_know=[_item("H", article_ids=("A1",))])
        coherence = {"results": [_fail("H", ["why_it_matters"], "why: fabricated", ("A1",))]}

        assert repair.build_repair_requests(draft, coherence)["requests"] == []

    def test_skips_mixed_headline_and_why(self):
        # A failure set that includes why_it_matters is NOT a subset of
        # {headline, summary}; MVP leaves it on the conservative drop path.
        draft = _draft(must_know=[_item("H", article_ids=("A1",))])
        coherence = {"results": [_fail("H", ["summary", "why_it_matters"], "two bad", ("A1",))]}

        assert repair.build_repair_requests(draft, coherence)["requests"] == []

    def test_skips_passing_story(self):
        draft = _draft(must_know=[_item("H", article_ids=("A1",))])
        coherence = {"results": [{"headline": "H", "article_ids": ["A1"], "pass": True}]}

        assert repair.build_repair_requests(draft, coherence)["requests"] == []

    def test_skips_unusable_failed_fields(self):
        # Missing / empty / non-list / unknown-field-name all stay on the drop path.
        for bad in (None, [], "headline", ["headline", 3], ["byline"]):
            draft = _draft(must_know=[_item("H", article_ids=("A1",))])
            result = _fail("H", bad, "bad", ("A1",))
            if bad is None:
                del result["failed_fields"]
            coherence = {"results": [result]}
            assert repair.build_repair_requests(draft, coherence)["requests"] == [], bad

    def test_request_carries_verbatim_fields_reason_and_ids(self):
        item = _item("Head", summary="Sum.", why="Why.", article_ids=("A5", "A2"))
        draft = _draft(must_know=[item])
        coherence = {"results": [_fail("Head", ["summary"], "summary: the exact reason", ("A5", "A2"))]}

        req = repair.build_repair_requests(draft, coherence)["requests"][0]

        assert req["fields"] == {"headline": "Head", "summary": "Sum.", "why_it_matters": "Why."}
        assert req["reason"] == "summary: the exact reason"
        assert set(req["article_ids"]) == {"A5", "A2"}

    def test_matches_by_article_ids_despite_headline_drift(self):
        # COHERENCE re-types the headline; a drifted string must still match on
        # the drift-proof article_ids so the repair request is still built.
        draft = _draft(must_know=[_item("Original headline", article_ids=("A7",))])
        coherence = {"results": [_fail("Original headline (retyped)", ["summary"], "summary: x", ("A7",))]}

        assert len(repair.build_repair_requests(draft, coherence)["requests"]) == 1


def _request(failed_fields, article_ids=("A1",), **fields):
    base = {"headline": "H", "summary": "S", "why_it_matters": "W"}
    base.update(fields)
    return {
        "article_ids": sorted(article_ids),
        "failed_fields": sorted(failed_fields),
        "reason": "r",
        "fields": base,
    }


def _repaired(article_ids=("A1",), action="corrected", **fields):
    return {"article_ids": sorted(article_ids), "action": action, **fields}


class TestApplyRepairs:
    def test_applies_valid_headline_repair(self):
        reqs = {"requests": [_request(["headline"], ("A5",))]}
        rep = {"results": [_repaired(("A5",), headline="Fixed headline")]}

        out = repair.apply_repairs(reqs, rep)

        assert len(out["applied"]) == 1
        entry = out["applied"][0]
        assert entry["ok"] is True
        assert entry["patched_fields"] == {"headline": "Fixed headline"}

    def test_applies_both_fields(self):
        reqs = {"requests": [_request(["headline", "summary"], ("A2",))]}
        rep = {"results": [_repaired(("A2",), headline="New H", summary="New S")]}

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is True
        assert entry["patched_fields"] == {"headline": "New H", "summary": "New S"}

    def test_guard_missing_from_repaired(self):
        reqs = {"requests": [_request(["headline"], ("A5",))]}
        rep = {"results": []}  # nothing came back for A5

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}
        assert "missing" in entry["guard"].lower()

    def test_guard_empty_or_whitespace_field(self):
        for bad in ("", "   ", "\n"):
            reqs = {"requests": [_request(["summary"], ("A5",))]}
            rep = {"results": [_repaired(("A5",), summary=bad)]}
            entry = repair.apply_repairs(reqs, rep)["applied"][0]
            assert entry["ok"] is False, repr(bad)
            assert entry["patched_fields"] == {}

    def test_guard_internal_id_leak(self):
        for leak in ("Now citing [A24] directly", "See cluster 3 for details"):
            reqs = {"requests": [_request(["summary"], ("A5",))]}
            rep = {"results": [_repaired(("A5",), summary=leak)]}
            entry = repair.apply_repairs(reqs, rep)["applied"][0]
            assert entry["ok"] is False, leak
            assert entry["patched_fields"] == {}

    def test_guard_repairs_unflagged_field(self):
        # Flagged headline only, but the repairer also (or instead) touched
        # summary -- a clean field must be untouchable, so this is a guard fail.
        reqs = {"requests": [_request(["headline"], ("A5",))]}
        rep = {"results": [_repaired(("A5",), headline="New H", summary="Sneaky edit")]}

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}

    def test_guard_missing_flagged_field(self):
        # Flagged both, only headline came back -- summary is still bad, so we
        # cannot keep the story; guard fails to the drop path.
        reqs = {"requests": [_request(["headline", "summary"], ("A5",))]}
        rep = {"results": [_repaired(("A5",), headline="New H")]}

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}

    def test_empty_article_ids_are_not_indexed(self):
        # A story with no sources yields article_ids == [] -> frozenset(). Two
        # such stories must NOT collide under a shared empty key (which would let
        # one story's repaired text attach to another). An empty-ids repaired
        # result is simply not indexed, so it reads as missing -> guard fail.
        reqs = {"requests": [_request(["headline"], article_ids=())]}
        rep = {"results": [_repaired((), headline="Sneaky cross-story text")]}

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}


def _applied(article_ids=("A1",), ok=True, patched_fields=None, guard=None):
    return {
        "article_ids": sorted(article_ids),
        "ok": ok,
        "patched_fields": patched_fields or ({"headline": "Fixed"} if ok else {}),
        "action": "corrected" if ok else None,
        "guard": guard,
    }


def _recheck(*results):
    return {"results": list(results)}


class TestBuildRepairResolution:
    def test_repaired_when_apply_ok_and_recheck_passes(self):
        applied = {"applied": [_applied(("A5",), patched_fields={"headline": "Fixed"})]}
        recheck = _recheck({"article_ids": ["A5"], "pass": True})

        res = repair.build_repair_resolution(applied, recheck)["results"][0]

        assert res["status"] == "repaired"
        assert res["recheck_pass"] is True
        assert res["patched_fields"] == {"headline": "Fixed"}

    def test_recheck_failed_when_recheck_fails(self):
        applied = {"applied": [_applied(("A5",))]}
        recheck = _recheck({"article_ids": ["A5"], "pass": False, "failed_fields": ["headline"]})

        res = repair.build_repair_resolution(applied, recheck)["results"][0]

        assert res["status"] == "recheck_failed"
        assert res["recheck_pass"] is False
        assert res["patched_fields"] == {}

    def test_recheck_missing_entry_fails_closed(self):
        # No re-check verdict for the story -> cannot confirm the fix -> drop.
        applied = {"applied": [_applied(("A5",))]}
        res = repair.build_repair_resolution(applied, _recheck())["results"][0]

        assert res["status"] == "recheck_failed"

    def test_guard_failed_propagates_without_consulting_recheck(self):
        # A guard-failed apply is never patched, even if a (stray) recheck entry
        # would pass -- recheck is only meaningful for a story we actually patched.
        applied = {"applied": [_applied(("A5",), ok=False, guard="missing from repaired output")]}
        recheck = _recheck({"article_ids": ["A5"], "pass": True})

        res = repair.build_repair_resolution(applied, recheck)["results"][0]

        assert res["status"] == "guard_failed"
        assert res["patched_fields"] == {}


class TestAppendRepairLog:
    def test_appends_one_json_line_per_event(self, tmp_path):
        log = tmp_path / "repair_log.jsonl"
        repair.append_repair_log(log, {"event": "one"})
        repair.append_repair_log(log, {"event": "two"})

        lines = log.read_text().splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["event"] for line in lines] == ["one", "two"]

    def test_creates_parent_and_file_if_missing(self, tmp_path):
        log = tmp_path / "nested" / "repair_log.jsonl"
        repair.append_repair_log(log, {"ok": True})

        assert log.exists()
        assert json.loads(log.read_text().strip())["ok"] is True


# --- merge.py ladder branch: consuming repair_resolution.json -----------------
# These live here (not test_merge.py) because they exercise the repair feature
# end-to-end through merge; test_merge.py owns merge's pre-repair behaviour.

import config  # noqa: E402
from merge import assemble_selections  # noqa: E402


def _mk_item(headline, article_id):
    return {
        "headline": headline,
        "summary": "Summary.",
        "why_it_matters": "Why.",
        "sources": [{"article_id": article_id}],
    }


def _write_merge_inputs(tmp_path, draft, coherence, resolution=None):
    (tmp_path / "draft_selections.json").write_text(json.dumps(draft))
    (tmp_path / "coherence_report.json").write_text(json.dumps(coherence))
    if resolution is not None:
        (tmp_path / "repair_resolution.json").write_text(json.dumps(resolution))


def _drop_setup(failed_fields=("headline",)):
    """A must_know that passes + a story merge drops today (fails `failed_fields`)."""
    draft = {
        "must_know": [_mk_item("good", "A1"), _mk_item("bad", "A2")],
        "should_know": [],
        "preheader": "p",
    }
    coherence = {
        "results": [
            {"headline": "good", "article_ids": ["A1"], "pass": True},
            {
                "headline": "bad",
                "article_ids": ["A2"],
                "pass": False,
                "reason": "wrong entity",
                "failed_fields": list(failed_fields),
            },
        ]
    }
    return draft, coherence


class TestMergeRepairLadder:
    def test_repaired_resolution_keeps_and_patches_story(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup()
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"headline": "bad, now correct"},
                    "recheck_pass": True,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good", "bad, now correct"]

    def test_recheck_failed_resolution_still_drops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup()
        resolution = {
            "results": [
                {"article_ids": ["A2"], "status": "recheck_failed", "patched_fields": {}, "recheck_pass": False}
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_no_resolution_file_drops_as_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup()
        _write_merge_inputs(tmp_path, draft, coherence, resolution=None)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_disabled_flag_ignores_resolution(self, tmp_path, monkeypatch):
        # Flag off: a stale/foreign repair_resolution.json must never alter drops.
        monkeypatch.setattr(config, "REPAIR_ENABLED", False)
        draft, coherence = _drop_setup()
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"headline": "bad, now correct"},
                    "recheck_pass": True,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_partial_coverage_drops(self, tmp_path, monkeypatch):
        # COHERENCE flagged BOTH headline and summary, but the resolution patches
        # only the headline. Keeping it would ship the still-bad summary. The
        # ladder cross-checks the patch against the checker's flagged fields and
        # drops on a mismatch. (In-process apply_repairs prevents this; the merge
        # consumer must also defend against a malformed/stale/hand-edited file.)
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup(failed_fields=("headline", "summary"))
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"headline": "only headline fixed"},
                    "recheck_pass": True,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_extra_unflagged_field_drops(self, tmp_path, monkeypatch):
        # Only headline was flagged, but the resolution also patches summary -- a
        # clean field must be untouchable, so the coverage mismatch drops it.
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup(failed_fields=("headline",))
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"headline": "fixed", "summary": "sneaky edit to a clean field"},
                    "recheck_pass": True,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_internal_id_leak_in_patch_drops(self, tmp_path, monkeypatch):
        # A patched value carrying an internal article id must never reach a
        # reader (cf. the [A221] blurb leak merge already guards). Reject -> drop.
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup(failed_fields=("summary",))
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"summary": "Beijing acted after [A24] reported the plan"},
                    "recheck_pass": True,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]

    def test_recheck_pass_not_true_ignored(self, tmp_path, monkeypatch):
        # Defense in depth: even if status says "repaired", a recheck_pass that is
        # not exactly True means the fix was never confirmed -> drop.
        monkeypatch.setattr(config, "REPAIR_ENABLED", True)
        draft, coherence = _drop_setup(failed_fields=("headline",))
        resolution = {
            "results": [
                {
                    "article_ids": ["A2"],
                    "status": "repaired",
                    "patched_fields": {"headline": "fixed"},
                    "recheck_pass": False,
                }
            ]
        }
        _write_merge_inputs(tmp_path, draft, coherence, resolution)

        out = assemble_selections(tmp_path)
        headlines = [a["headline"] for a in json.loads(out.read_text())["must_know"]]

        assert headlines == ["good"]


# --- Integration: the real run-245 fixture (the data that motivated repair) ---

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "coherence_faithful"


class TestRealRun245Fixture:
    def test_builds_requests_only_for_the_three_whole_story_drops(self):
        """On run 245 the reframed COHERENCE dropped 3 WHOLE stories over a
        headline/summary failure (Burnham/summary, Israel-centrifuges/headline,
        China/summary) and blanked 3 why_it_matters (Zelensky, Philippine, Ebola).
        Repair must target exactly the 3 whole-story drops and leave the 3
        why-only failures on merge's existing blank path."""
        draft = json.loads((FIXTURE_DIR / "draft_selections.json").read_text())
        coherence = json.loads((FIXTURE_DIR / "coherence_report.json").read_text())

        reqs = repair.build_repair_requests(draft, coherence)["requests"]
        headlines = {r["fields"]["headline"] for r in reqs}

        assert len(reqs) == 3
        assert any("Burnham" in h for h in headlines)
        assert any("centrifuges" in h for h in headlines)
        assert any("China considers tighter export controls" in h for h in headlines)
        # The why_it_matters-only failures must NOT be repaired.
        assert not any(kw in h for h in headlines for kw in ("Zelensky", "Ebola", "Philippine"))
