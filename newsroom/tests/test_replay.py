"""Tests for replay.py: re-run a finished run's TAIL from its archived artifacts.

On 2026-09-17 a deployed thread-link change could not be verified locally before the next
scheduled run. `--write-only` renders, but only with THREADS_ENABLED set and a hand-written
thread_assignments.json -- and that file is written AFTER archive_run_artifacts sweeps
claude_input/ (see the comment on the thread block in run.py), so no archived run carries it.
The check that did work was a throwaway script against a hand-built assignment file, which is
exactly the thing that should be a harness.

Replay closes that: materialise what WAS archived, reconstruct the thread assignments from the
archived thread_links.json trace when the authoritative file is absent, render, and report.
No model calls, and no writes to the database -- a verification surface that mutates the run it
is verifying is not one.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import db
import replay
import threads

REPO = Path(__file__).parent.parent.parent
MIGRATIONS_DIR = REPO / "migrations"
# config.TEMPLATE_FILE resolves against the prod image layout (newsroom/ as the root); under the
# CI container the repo mounts one level up, so point at the real file rather than skipping.
TEMPLATE_FILE = REPO / "newsroom" / "templates" / "digest-template.html"
STYLES_FILE = REPO / "newsroom" / "templates" / "digest.css"

LINKS = replay.LINKS_ARTIFACT
ASSIGNMENTS = replay.ASSIGNMENTS_ARTIFACT


def _selections(cluster_id="Iran war"):
    """Minimal selections the renderer accepts: the kitchen-sink fixture carries no cluster_id,
    and cluster_id is what attach_thread_context matches an assignment on."""
    return {
        "preheader": "Today's briefing",
        "must_know": [
            {
                "headline": "US strikes IRGC positions",
                "summary": "A strike wave hit three sites.",
                "why_it_matters": "It widens the war.",
                "cluster_id": cluster_id,
                "sources": [{"name": "Reuters", "bias": "center", "url": "https://example.test/a"}],
            }
        ],
        "should_know": [],
    }


@pytest.fixture
def archived(tmp_path, monkeypatch):
    """A finished run whose artifacts are archived, with one continuing thread in the DB."""
    dbp = tmp_path / "replay.db"
    db._state = db._State()
    db.init(dbp, MIGRATIONS_DIR)
    monkeypatch.setattr(config, "DB_PATH", dbp)
    monkeypatch.setattr(config, "THREADS_ENABLED", True)
    monkeypatch.setattr(config, "TEMPLATE_FILE", TEMPLATE_FILE)
    monkeypatch.setattr(config, "STYLES_FILE", STYLES_FILE)

    conn = sqlite3.connect(dbp)
    conn.execute("INSERT INTO digest_runs (git_sha) VALUES ('r1')")
    conn.commit()
    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran war", run_id=1)
    store.record_installment(tid, 1, "Iran war", is_new=True)
    store.record_installment(tid, 2, "Iran war", is_new=False)  # day 2 -- the badge threshold
    conn.close()

    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)
    db.record_run_artifact("selections.json", json.dumps(_selections()))
    db.record_run_artifact(
        "thread_links.json",
        json.dumps(
            {
                "linker_ok": True,
                "stories": [
                    {"story_index": 0, "label": "Iran war", "proposed_thread": tid, "outcome": "continued"},
                    {"story_index": 1, "label": "Brand new story", "proposed_thread": None, "outcome": "new"},
                ],
            }
        ),
    )
    return {"run_id": run_id, "thread_id": tid, "db_path": dbp}


# --- materialise ---------------------------------------------------------------------------


def test_materialize_writes_every_archived_artifact(archived, tmp_path):
    dest = tmp_path / "out"
    names = replay.materialize(archived["run_id"], dest)

    assert set(names) == {"selections.json", "thread_links.json"}
    assert json.loads((dest / "selections.json").read_text())["must_know"][0]["cluster_id"] == "Iran war"


def test_materialize_refuses_a_run_with_nothing_archived(archived, tmp_path):
    """A silent empty dir would replay as 'no findings' -- indistinguishable from a clean run."""
    with pytest.raises(LookupError, match="no archived artifacts"):
        replay.materialize(archived["run_id"] + 999, tmp_path / "out")


# --- assignment reconstruction -------------------------------------------------------------


def test_assignments_from_trace_keeps_continued_stories():
    trace = {
        "stories": [
            {"label": "Iran war", "proposed_thread": 7, "outcome": "continued"},
            {"label": "New thing", "proposed_thread": None, "outcome": "new"},
        ]
    }
    assert replay.assignments_from_trace(trace) == [{"story": "Iran war", "thread_id": 7, "is_new": False}]


def test_assignments_from_trace_drops_a_refused_proposal():
    """A refused proposal created a NEW thread whose id the trace never records; claiming the
    proposed one would attach the wrong thread's history to the story.

    The shape here is the one threads.py:802 actually emits -- `outcome` is only ever "continued"
    or "new", and a refusal shows up as "new" with `proposed_thread` STILL SET plus a `refused`
    reason. An earlier version of this test asserted on `outcome: "refused"`, which the producer
    never writes, so it proved nothing about real traces.
    """
    trace = {"stories": [{"label": "Iran war", "proposed_thread": 7, "refused": "already_claimed", "outcome": "new"}]}
    assert replay.assignments_from_trace(trace) == []


def test_assignments_from_trace_tolerates_a_trace_without_stories():
    assert replay.assignments_from_trace({}) == []
    assert replay.assignments_from_trace({"stories": "not a list"}) == []


# --- the replay itself ---------------------------------------------------------------------


def test_replay_renders_the_thread_link_for_a_continuing_story(archived, tmp_path, monkeypatch):
    """The regression this harness exists for: a shipped thread link nothing local could check."""
    monkeypatch.setenv("DIGEST_DOMAIN", "news.example.test")
    report = replay.replay(archived["run_id"], tmp_path / "out")

    want = f"https://news.example.test/thread/{archived['thread_id']}"
    assert want in report.web_thread_links, report.web_thread_links
    assert want in report.email_thread_links, report.email_thread_links
    assert report.badges >= 1


def test_replay_prefers_an_archived_assignments_file_over_the_trace(archived, tmp_path):
    """Once run.py archives the authoritative file, replay must stop guessing from the trace."""
    db.record_run_artifact(
        "thread_assignments.json",
        json.dumps([{"story": "Iran war", "thread_id": archived["thread_id"], "is_new": False}]),
    )
    report = replay.replay(archived["run_id"], tmp_path / "out")

    assert report.assignments_source == ASSIGNMENTS
    assert report.badges >= 1


def test_replay_falls_back_to_the_trace_when_assignments_were_never_archived(archived, tmp_path):
    report = replay.replay(archived["run_id"], tmp_path / "out")
    assert report.assignments_source == LINKS


def test_a_leftover_derived_assignments_file_is_not_mistaken_for_archived_evidence(archived, tmp_path):
    """Re-running into the same directory is the normal case: `bin/replay N` defaults to
    data/replay/runN. Found by running it twice against run 285 -- the second pass reported the
    assignments as archived when they were its own reconstruction from the previous pass, which
    would hide the fact that a run predates the archival fix.
    """
    dest = tmp_path / "out"
    first = replay.replay(archived["run_id"], dest)
    assert first.assignments_source == LINKS

    second = replay.replay(archived["run_id"], dest)
    assert second.assignments_source == LINKS, "its own derivation must not read as archived evidence"


def test_a_hand_placed_assignments_file_is_used_but_never_deleted_or_called_archived(archived, tmp_path):
    """--out invites reusing a directory to test a hypothesis by hand.

    An earlier version deleted any thread_assignments.json the run had not archived, silently, so
    a hand-written file vanished. Use it -- that is what placing it means -- but never report it
    as archived evidence, and never remove someone else's file.
    """
    dest = tmp_path / "out"
    replay.materialize(archived["run_id"], dest)
    mine = dest / ASSIGNMENTS
    mine.write_text(json.dumps([{"story": "Iran war", "thread_id": archived["thread_id"], "is_new": False}]))

    report = replay.replay(archived["run_id"], dest)

    assert mine.exists(), "replay deleted a file it did not write"
    assert report.assignments_source == LINKS, "a derivable trace still wins, and says so"


def test_replay_does_not_write_to_the_database(archived, tmp_path):
    """A surface that records a run while verifying it corrupts the thing it measures."""
    before = _db_snapshot(archived["db_path"])
    replay.replay(archived["run_id"], tmp_path / "out")
    assert _db_snapshot(archived["db_path"]) == before


def _db_snapshot(path):
    """Full table CONTENT, not row counts.

    Counts are blind to an UPDATE: `attach_thread_context` hands a read-write connection to
    ThreadStore, whose touch_thread/create_thread neighbours of the read-only render_context do
    write. A counts-only snapshot would let a future "cache the delta here" change silently mutate
    threads.label/last_run_id in a prod-cloned database while this test still passed.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: hashlib.sha256(repr(sorted(conn.execute(f"SELECT * FROM {t}").fetchall())).encode()).hexdigest()
            for t in ("digest_runs", "digests", "run_artifacts", "threads", "thread_installments", "shown_narratives")
        }
    finally:
        conn.close()


def test_replay_reports_coherence_failure_kinds(archived, tmp_path):
    """The other change shipped unverified on 2026-09-17: the contradicted/unsupported label."""
    db.record_run_artifact(
        "coherence_report.json",
        json.dumps(
            {
                "results": [
                    {
                        "headline": "a",
                        "pass": False,
                        "failed_fields": ["summary"],
                        "failure_kinds": {"summary": "contradicted"},
                    },
                    {
                        "headline": "b",
                        "pass": False,
                        "failed_fields": ["why_it_matters"],
                        "failure_kinds": {"why_it_matters": "unsupported"},
                    },
                    {"headline": "c", "pass": True},
                ]
            }
        ),
    )
    report = replay.replay(archived["run_id"], tmp_path / "out")
    assert report.coherence_kinds == {"contradicted": 1, "unsupported": 1, "unlabelled": 0}


def test_the_web_render_is_presentation_faithful(archived, tmp_path):
    """A render left at the template stage invites misreading.

    Screenshotting the first version of this harness showed `{{DATE}}` and `{{ISSUE_LABEL}}`
    sitting in the output with no CSS: structurally correct, but not what a reader sees, so
    anyone eyeballing it would report styling bugs that do not exist.
    """
    report = replay.replay(archived["run_id"], tmp_path / "out")
    html = report.web_path.read_text()

    assert "{{" not in html, "unsubstituted placeholders left in the web render"
    assert "--" in html and "<style" in html, "the stylesheet was never injected"


def test_replay_writes_both_renders_for_eyeballing(archived, tmp_path):
    out = tmp_path / "out"
    report = replay.replay(archived["run_id"], out)

    assert report.web_path.exists() and report.web_path.read_text().strip()
    assert report.email_path.exists() and report.email_path.read_text().strip()
    # Under the replay dir, never data/output -- replay must not overwrite a real digest.
    assert report.web_path.is_relative_to(out)


def test_the_pipeline_archives_the_assignments_a_replay_needs(tmp_path, monkeypatch):
    """run.py must RECORD thread_assignments.json, not just write it.

    The file is written after archive_run_artifacts has already swept claude_input/, so without an
    explicit record_run_artifact it never reaches the archive -- and a replay then has to infer
    assignments from the link trace, which cannot recover a refused proposal's real thread.
    """
    import run
    import thread_synthesis
    import threads as threads_mod

    cid = tmp_path / "claude_input"
    cid.mkdir()
    (cid / "clusters.json").write_text("{}")
    (cid / "selected.json").write_text("{}")

    dbp = tmp_path / "run.db"
    db._state = db._State()
    db.init(dbp, MIGRATIONS_DIR)
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", cid)
    monkeypatch.setattr(run, "DB_PATH", dbp)
    monkeypatch.setattr(run, "THREADS_ENABLED", True)

    class _Assignment:
        thread_id, is_new, cluster_story = 7, False, "Iran war"

    monkeypatch.setattr(threads_mod, "selected_labels", lambda *_: ["Iran war"])
    monkeypatch.setattr(threads_mod, "resolve_threads", lambda *a, **k: [_Assignment()])
    monkeypatch.setattr(thread_synthesis, "synthesize_threads", lambda *a, **k: ([], 0))

    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)
    run._process_story_threads()

    conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT content FROM run_artifacts WHERE run_id = ? AND artifact_name = 'thread_assignments.json'",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row, "thread_assignments.json was not archived, so this run is not replayable"
    assert json.loads(row[0]) == [{"thread_id": 7, "is_new": False, "story": "Iran war"}]


def test_replay_surfaces_run_health_violations(archived, tmp_path):
    """Replaying a run reports what the invariants said about it, so a bad run reads as bad."""
    report = replay.replay(archived["run_id"], tmp_path / "out")
    assert isinstance(report.violations, list)
    # This synthetic run recorded no usage and shipped nothing, so it must NOT read as healthy.
    assert report.violations, "a run with no usage and no recipients should violate something"


def test_replay_evaluates_the_invariants_in_a_fresh_process(archived, tmp_path):
    """From the CLI nobody has called db.init, so the module has no database path yet.

    Found by running the harness for real against run 285: every invariant came back as
    MALFORMED_HEALTH, which reads as "this run is broken" when it actually meant "I never opened
    the database". Replay must establish the path itself -- without applying migrations, which
    would be a write.
    """
    db._state = db._State()  # a fresh interpreter, as `bin/replay` gets

    report = replay.replay(archived["run_id"], tmp_path / "out")

    assert not any("MALFORMED_HEALTH" in v for v in report.violations), report.violations
