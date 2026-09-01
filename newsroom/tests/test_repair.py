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
from datetime import UTC, datetime
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

    def test_builds_request_for_why_it_matters_only_failure(self):
        # why_it_matters is repaired like any other field; blanking stays as the
        # fail-closed fallback when repair does not produce a clean patch.
        draft = _draft(must_know=[_item("H", article_ids=("A1",))])
        coherence = {"results": [_fail("H", ["why_it_matters"], "why: fabricated", ("A1",))]}

        out = repair.build_repair_requests(draft, coherence)

        assert [r["failed_fields"] for r in out["requests"]] == [["why_it_matters"]]

    def test_builds_request_for_mixed_summary_and_why(self):
        # A mixed set is a clean subset of the repairable fields, so the whole
        # story is repaired rather than dropped.
        draft = _draft(must_know=[_item("H", article_ids=("A1",))])
        coherence = {"results": [_fail("H", ["summary", "why_it_matters"], "two bad", ("A1",))]}

        out = repair.build_repair_requests(draft, coherence)

        assert set(out["requests"][0]["failed_fields"]) == {"summary", "why_it_matters"}

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


class TestBackfillRunId:
    """On --resume the repair phase runs BEFORE db.start_run, so every event it logs
    carries run_id null -- the corpus loses attribution on exactly the recovered runs.
    The run id does not exist yet at write time, so it is written back once it does."""

    @staticmethod
    def _event(log, *, run_id, ids, proc=repair.PROCESS_TOKEN):
        event = {"run_id": run_id, "ts": datetime.now(UTC).isoformat(), "article_ids": ids}
        if proc is not None:
            event["proc"] = proc
        repair.append_repair_log(log, event)

    def test_claims_this_process_s_unattributed_events_only(self, tmp_path):
        log = tmp_path / "repair_log.jsonl"
        # Another process's unattributed event: a null is an honest unknown, and stamping
        # this run's id onto it would be a false fact in the eval's ground truth.
        self._event(log, run_id=None, ids=["A9"], proc="another-process")
        # An event from before this field existed: unclaimable, and not this run's to guess.
        self._event(log, run_id=None, ids=["A8"], proc=None)
        # Written by THIS process, before the run row existed.
        self._event(log, run_id=None, ids=["A1"])
        # Already attributed -- never re-stamped.
        self._event(log, run_id=7, ids=["A2"])

        assert repair.backfill_run_id(log, 42) == 1

        got = [json.loads(line) for line in log.read_text().splitlines()]
        assert [e["run_id"] for e in got] == [None, None, 42, 7]
        assert [e["article_ids"] for e in got] == [["A9"], ["A8"], ["A1"], ["A2"]]

    def test_a_log_with_nothing_to_claim_is_left_byte_for_byte(self, tmp_path):
        log = tmp_path / "repair_log.jsonl"
        self._event(log, run_id=7, ids=["A2"])
        before = log.read_bytes()

        assert repair.backfill_run_id(log, 42) == 0
        assert log.read_bytes() == before

    def test_a_malformed_line_is_preserved_not_dropped(self, tmp_path):
        # The corpus is the eval's ground truth; a rewrite that silently drops a line it
        # cannot parse is worse than the missing attribution it is fixing.
        log = tmp_path / "repair_log.jsonl"
        log.write_text('{"run_id": null, "ts": "not a date"}\nnot json at all\n')
        self._event(log, run_id=None, ids=["A1"])

        assert repair.backfill_run_id(log, 42) == 1

        lines = log.read_text().splitlines()
        assert lines[0] == '{"run_id": null, "ts": "not a date"}'
        assert lines[1] == "not json at all"
        assert json.loads(lines[2])["run_id"] == 42

    def test_an_exotic_separator_inside_a_junk_line_is_not_reflowed(self, tmp_path):
        # splitlines() also breaks on \x0b/\x1c/\u2028; using it would turn ONE unreadable
        # line into two, corrupting exactly the lines this promises to leave alone.
        log = tmp_path / "repair_log.jsonl"
        log.write_bytes(b"garbage\x0bmore garbage\n")
        self._event(log, run_id=None, ids=["A1"])

        assert repair.backfill_run_id(log, 42) == 1

        lines = log.read_bytes().split(b"\n")
        assert lines[0] == b"garbage\x0bmore garbage"
        assert json.loads(lines[1])["run_id"] == 42

    def test_an_event_appended_after_a_torn_line_stays_readable(self, tmp_path):
        # A killed run leaves a line with no terminator. Appending straight onto it would
        # splice this run's event into that wreckage: one unparseable line, and the event
        # both unattributed and unreadable.
        log = tmp_path / "repair_log.jsonl"
        log.write_bytes(b'{"run_id": 7, "partial": ')
        self._event(log, run_id=None, ids=["A1"])

        assert repair.backfill_run_id(log, 42) == 1

        lines = log.read_bytes().split(b"\n")
        assert lines[0] == b'{"run_id": 7, "partial": '
        assert json.loads(lines[1])["run_id"] == 42

    def test_a_corpus_that_grew_mid_rewrite_is_left_alone(self, tmp_path, monkeypatch):
        # The rewrite is not append-safe and nothing upstream serializes runs. A missing
        # run_id is recoverable; an event dropped by a clobbering rewrite is not.
        log = tmp_path / "repair_log.jsonl"
        self._event(log, run_id=None, ids=["A1"])
        real_open = repair.Path.open
        written: list = []

        def append_then_open(self, *a, **k):
            if "w" in str(a[:1]) + str(k.get("mode", "")):
                written.append(self)
                TestBackfillRunId._event(log, run_id=7, ids=["CONCURRENT"])
            return real_open(self, *a, **k)

        monkeypatch.setattr(repair.Path, "open", append_then_open)

        assert repair.backfill_run_id(log, 42) == 0

        # The scratch file is per-process. A fixed ".tmp" would be shared by concurrent
        # backfills, and the size guard watches the CORPUS -- so one process could rename
        # another's half-written copy over it and never notice.
        assert written and repair.PROCESS_TOKEN in written[0].name, written

        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert [e["article_ids"] for e in events] == [["A1"], ["CONCURRENT"]]
        assert not list(tmp_path.glob("repair_log.jsonl*.tmp"))

    def test_a_non_utf8_corpus_neither_raises_nor_is_corrupted(self, tmp_path):
        # This writer emits ASCII, but the corpus is a plain file on a shared volume that
        # a hand edit or another tool can leave invalid. read_text would raise
        # UnicodeDecodeError -- a ValueError, not an OSError -- from the FIRST statement of
        # the resume tail's try, aborting a run whose whole job is recovery.
        log = tmp_path / "repair_log.jsonl"
        log.write_bytes(b'{"run_id": null, "reason": "caf\xc3"}\n')
        self._event(log, run_id=None, ids=["A1"])

        assert repair.backfill_run_id(log, 42) == 1

        lines = log.read_bytes().splitlines()
        assert lines[0] == b'{"run_id": null, "reason": "caf\xc3"}'
        assert json.loads(lines[1])["run_id"] == 42

    def test_the_corpus_keeps_its_permissions(self, tmp_path):
        # replace() takes the tmp file's mode; the corpus lives on a shared data volume.
        log = tmp_path / "repair_log.jsonl"
        self._event(log, run_id=None, ids=["A1"])
        log.chmod(0o640)

        repair.backfill_run_id(log, 42)

        assert log.stat().st_mode & 0o777 == 0o640

    def test_no_log_is_not_an_error(self, tmp_path):
        assert repair.backfill_run_id(tmp_path / "absent.jsonl", 42) == 0


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
    def test_builds_requests_for_every_failure_including_why_only(self):
        """Real-data check that every coherence failure builds a request: on this fixture
        3 headline/summary failures (Burnham, Israel-centrifuges, China) and 3 why-only
        failures (Zelensky, Philippine, Ebola). All 6 are repairable."""
        draft = json.loads((FIXTURE_DIR / "draft_selections.json").read_text())
        # coherence_report.json is gitignored (the eval regenerates it), so read the
        # committed frozen copy -- otherwise this test breaks on a clean checkout.
        coherence = json.loads((FIXTURE_DIR / "coherence_report.frozen.json").read_text())

        reqs = repair.build_repair_requests(draft, coherence)["requests"]
        headlines = {r["fields"]["headline"] for r in reqs}

        assert len(reqs) == 6
        assert any("Burnham" in h for h in headlines)
        assert any("centrifuges" in h for h in headlines)
        assert any("China considers tighter export controls" in h for h in headlines)
        assert any("Zelensky" in h for h in headlines)
        assert any("Ebola" in h for h in headlines)
        assert any("Philippine" in h for h in headlines)
        why_only = [r for r in reqs if r["failed_fields"] == ["why_it_matters"]]
        assert len(why_only) == 3


class TestMultipleCoherenceFailuresForOneStory:
    """COHERENCE may return more than one failing result for a story. merge unions their
    failed_fields and requires the patch to match that union exactly, so a repair request built
    from only the FIRST match asks for too little: every guard passes, then merge rejects the
    patch as a field-set mismatch and the story is dropped after a paid call."""

    def test_request_fields_match_what_merge_will_require(self):
        import merge

        results = [
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "summary: bad",
                "failed_fields": ["summary"],
            },
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "why: bad",
                "failed_fields": ["why_it_matters"],
            },
        ]
        draft = {
            "must_know": [{"headline": "H", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}]
        }

        reqs = repair.build_repair_requests(draft, {"results": results})["requests"]
        assert len(reqs) == 1
        assert set(reqs[0]["failed_fields"]) == merge._repairable_flagged_fields(results)

    def test_reason_carries_every_matching_failure(self):
        results = [
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "summary: bad",
                "failed_fields": ["summary"],
            },
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "why: bad",
                "failed_fields": ["why_it_matters"],
            },
        ]
        draft = {
            "must_know": [{"headline": "H", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}]
        }
        reason = repair.build_repair_requests(draft, {"results": results})["requests"][0]["reason"]
        assert "summary: bad" in reason and "why: bad" in reason

    def test_a_non_string_reason_does_not_kill_the_phase(self):
        """`reason` is model-generated and validated nowhere -- not by validate_coherence, which
        checks only `pass` and `failed_fields`. Joining reasons made a list-valued reason a
        TypeError that _run_repair_phase_best_effort swallows, dropping EVERY repairable story
        that run. merge already coerces this same field on the blanking path."""
        results = [
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": ["clause A", "clause B"],
                "failed_fields": ["summary"],
            },
            {"headline": "H", "article_ids": ["A1"], "pass": False, "reason": 7, "failed_fields": ["why_it_matters"]},
        ]
        draft = {
            "must_know": [{"headline": "H", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}]
        }
        req = repair.build_repair_requests(draft, {"results": results})["requests"][0]
        assert isinstance(req["reason"], str)
        assert "clause A" in req["reason"]

    def test_one_non_repairable_failure_skips_the_story(self):
        """merge returns None if ANY matching failure names an unrepairable field. repair must
        agree, or it pays for a repair merge will refuse."""
        results = [
            {
                "headline": "H",
                "article_ids": ["A1"],
                "pass": False,
                "reason": "summary: bad",
                "failed_fields": ["summary"],
            },
            {"headline": "H", "article_ids": ["A1"], "pass": False, "reason": "??", "failed_fields": ["preheader"]},
        ]
        draft = {
            "must_know": [{"headline": "H", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}]
        }
        assert repair.build_repair_requests(draft, {"results": results})["requests"] == []


class TestApplyRepairsSplitAcrossObjects:
    """The repairer may answer per-FIELD instead of per-STORY, returning
    ``{article_ids:[A13], headline:...}`` then ``{article_ids:[A13], why_it_matters:...}``
    for a story flagged on both. A plain last-wins index on frozenset(article_ids)
    discards the first patch, the guard then sees an incomplete field set, and a story
    that was fully repaired is dropped."""

    def test_merges_two_objects_for_one_story(self):
        # Flagged both fields, returned in SEPARATE objects sharing article_ids. Both
        # were genuinely repaired, so the story must be kept.
        reqs = {"requests": [_request(["headline", "why_it_matters"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), action="corrected", headline="Fixed headline"),
                _repaired(("A13",), action="deleted_unsupported", why_it_matters="Fixed why"),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is True
        assert entry["patched_fields"] == {
            "headline": "Fixed headline",
            "why_it_matters": "Fixed why",
        }

    def test_order_decides_because_the_last_object_is_the_final_answer(self):
        """Object order is meaningful, not incidental. A last object that NARROWS to exactly the
        flagged set is a withdrawal and is honoured; a last object naming an unflagged field is an
        out-of-scope final answer and drops the story. Matching an earlier object instead would let
        that out-of-scope answer be ignored -- the smuggling case below."""
        reqs = {"requests": [_request(["headline"], ("A13",))]}
        compliant = _repaired(("A13",), action="corrected", headline="Good headline")
        overreaching = _repaired(("A13",), headline="Good headline", summary="unasked for")

        kept = repair.apply_repairs(reqs, {"results": [overreaching, compliant]})["applied"][0]
        assert kept["ok"] is True
        assert kept["patched_fields"] == {"headline": "Good headline"}

        dropped = repair.apply_repairs(reqs, {"results": [compliant, overreaching]})["applied"][0]
        assert dropped["ok"] is False
        assert dropped["patched_fields"] == {}

    def test_conflicting_text_for_one_field_is_logged(self, caplog):
        """Last-wins on a genuine conflict is a silent loss: the discarded text never appears in
        repair_log.jsonl, so the unmeasured take-last choice cannot be measured after the fact."""
        reqs = {"requests": [_request(["summary"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), summary="A full, correct, well-sourced summary."),
                _repaired(("A13",), summary="tbd"),
            ]
        }
        with caplog.at_level("WARNING"):
            repair.apply_repairs(reqs, rep)
        assert any("conflicting" in r.getMessage() for r in caplog.records)

    def test_split_objects_still_guarded_on_each_field(self):
        # Merging must not weaken the per-field guards: one half empty is still a fail.
        reqs = {"requests": [_request(["headline", "summary"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), headline="Fixed headline"),
                _repaired(("A13",), summary="   "),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}

    def test_over_repair_then_self_correct_keeps_the_story(self):
        """A union merge cannot express the model WITHDRAWING a field: it touches an
        unflagged field, notices, and re-emits with only the flagged one. Unioning keeps
        the withdrawn field, `set(present) != flagged` rejects, and the story drops. Hence
        the take-last-exact-match rule ahead of the union.
        """
        reqs = {"requests": [_request(["summary"], ("A3",))]}
        rep = {
            "results": [
                _repaired(("A3",), summary="draft", why_it_matters="oops, touched this"),
                _repaired(("A3",), summary="final summary"),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is True
        assert entry["patched_fields"] == {"summary": "final summary"}

    def test_restated_same_field_takes_the_later_value(self):
        # NOT a conflict-drop: a restated field is the model correcting itself, and
        # dropping would discard the correction in the one stage that exists to save a
        # story. Still guarded -- the later value has to pass every check on its own.
        reqs = {"requests": [_request(["headline"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), headline="Fixed headline with a typoo"),
                _repaired(("A13",), headline="Fixed headline"),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is True
        assert entry["patched_fields"] == {"headline": "Fixed headline"}

    def test_restated_field_is_still_guarded(self):
        # Taking the later value must not weaken the guards: a self-correction INTO an
        # empty field is still a fail.
        reqs = {"requests": [_request(["headline"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), headline="Fixed headline"),
                _repaired(("A13",), headline="   "),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}

    def test_split_objects_cannot_smuggle_an_unflagged_field(self):
        # The "clean fields are untouchable" guard must survive merging: a second
        # object naming a field that was never flagged still fails the story.
        reqs = {"requests": [_request(["headline"], ("A13",))]}
        rep = {
            "results": [
                _repaired(("A13",), headline="Fixed headline"),
                _repaired(("A13",), summary="Sneaky edit"),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}

    def test_empty_ids_objects_still_not_indexed_when_merging(self):
        # The empty-key collision guard must not be lost in the merge: two storyless
        # results must not pool under frozenset() and patch an unrelated story.
        reqs = {"requests": [_request(["headline"], article_ids=())]}
        rep = {
            "results": [
                _repaired((), headline="Cross-story text"),
                _repaired((), summary="More cross-story text"),
            ]
        }

        entry = repair.apply_repairs(reqs, rep)["applied"][0]

        assert entry["ok"] is False
        assert entry["patched_fields"] == {}


class TestRecheckVerdictSplitAcrossObjects:
    """A split RE-CHECK verdict must fail closed, not resolve by emission order.

    Last-wins is fail-OPEN for verdicts: a re-checker emitting {pass:false} then
    {pass:true} would ship text the checker explicitly rejected, and the reverse order
    would drop it -- the outcome decided by which object came last.
    """

    def _applied(self):
        return {
            "applied": [
                {
                    "article_ids": ["A13"],
                    "ok": True,
                    "patched_fields": {"why_it_matters": "new"},
                    "action": "corrected",
                    "guard": None,
                }
            ]
        }

    @staticmethod
    def _verdict(passed):
        v = {"article_ids": ["A13"], "pass": passed}
        if not passed:
            v |= {"failed_fields": ["why_it_matters"], "reason": "still bad"}
        return v

    def test_disagreeing_verdicts_drop_regardless_of_order(self):
        fail, ok = self._verdict(False), self._verdict(True)
        for order in ([fail, ok], [ok, fail]):
            out = repair.build_repair_resolution(self._applied(), {"results": order})
            assert out["results"][0]["status"] == "recheck_failed", order
            assert out["results"][0]["recheck_pass"] is not True, order

    def test_agreeing_duplicate_passes_still_pass(self):
        # Two objects that AGREE are not a contradiction -- a split-but-consistent
        # verdict must not cost a story that was genuinely re-verified.
        ok = self._verdict(True)
        out = repair.build_repair_resolution(self._applied(), {"results": [ok, dict(ok)]})
        assert out["results"][0]["status"] == "repaired"

    def test_non_dict_verdict_does_not_raise(self):
        # validate_recheck_report only checks that `results` is a list, so a string
        # element reaches the indexer. Raising here loses EVERY repair in the run.
        out = repair.build_repair_resolution(self._applied(), {"results": ["A13 passes"]})
        assert out["results"][0]["status"] == "recheck_failed"
