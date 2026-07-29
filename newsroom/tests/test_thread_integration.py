"""Integration test for the run.py thread wiring (_process_story_threads).

Exercises the production seam end-to-end with the LLM calls faked: stages claude_input,
runs the real run._process_story_threads (identity + synthesis + persistence + health +
artifacts), and asserts the wiring holds together. This is the path the scratch trials
proved manually; this pins it in CI so it can't regress.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import run
import thread_synthesis
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A migrated DB + staged claude_input with one continuing story (Iran) and one new (EU)."""
    cid = tmp_path / "claude_input"
    cid.mkdir()
    (cid / "clusters.json").write_text(
        json.dumps(
            {
                "clusters": [
                    {"story": "Iran nuclear deal", "article_ids": ["A1", "A2"]},
                    {"story": "EU AI regulation", "article_ids": ["A3"]},
                ]
            }
        )
    )
    (cid / "selected.json").write_text(
        json.dumps(
            {
                "must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}],
                "should_know": [{"cluster_index": 1, "article_ids": ["A3"]}],
            }
        )
    )
    (cid / "articles_1.csv").write_text(
        "article_id,title,summary\nA1,Iran deal,talks\nA2,Iran Hormuz,shipping\nA3,EU AI act,rules\n"
    )

    db._state = db._State()
    dbp = tmp_path / "t.db"
    db.init(dbp, MIGRATIONS_DIR)
    monkeypatch.setattr(run, "DB_PATH", dbp)
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", cid)
    monkeypatch.setattr(run, "THREADS_ENABLED", True)

    # Seed a prior Iran thread (run 1) so today's Iran story continues it.
    seed = sqlite3.connect(dbp)
    seed.execute("INSERT INTO digest_runs (git_sha) VALUES ('r1')")  # run 1
    seed.commit()
    store = threads.ThreadStore(seed)
    iran_tid = store.create_thread("Iran nuclear deal", run_id=1)
    store.record_installment(iran_tid, 1, "Iran nuclear deal", is_new=True)
    seed.close()
    return {"cid": cid, "dbp": dbp, "iran_tid": iran_tid}


def test_process_story_threads_wiring_end_to_end(staged, monkeypatch):
    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)

    # Fake the LLM: link the Iran story to its existing thread, EU stays new; trivial synth+audit.
    monkeypatch.setattr(
        threads,
        "link_threads",
        lambda active, labels, **k: [
            next((t.thread_id for t in active if "Iran" in t.label), None) if "Iran" in lab else None for lab in labels
        ],
    )
    monkeypatch.setattr(
        thread_synthesis,
        "synthesize_installment",
        lambda *a, **k: {"whats_new": [{"fact": "Iran talks resumed in Geneva.", "sources": ["A1"]}]},
    )
    monkeypatch.setattr(thread_synthesis, "audit_whats_new", lambda *a, **k: [True])

    run._process_story_threads()  # the real wiring

    cid, dbp, iran_tid = staged["cid"], staged["dbp"], staged["iran_tid"]
    assigns = json.loads((cid / "thread_assignments.json").read_text())
    installments = json.loads((cid / "thread_installments.json").read_text())
    assert {a["story"] for a in assigns} == {"Iran nuclear deal", "EU AI regulation"}
    assert any(not a["is_new"] and a["thread_id"] == iran_tid for a in assigns)  # Iran continued
    assert any(a["is_new"] and a["story"] == "EU AI regulation" for a in assigns)  # EU new
    assert [i["thread_id"] for i in installments] == [iran_tid]  # only the continuing thread synthesized

    conn = sqlite3.connect(dbp)
    # health row recorded for this run (1 synthesized, 0 audit failures)
    health = conn.execute(
        "SELECT threads_synthesized, audit_failures FROM thread_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert health == (1, 0)
    # the verified installment (the delta source + next-day memory) is persisted on the Iran thread
    content = conn.execute(
        "SELECT content FROM thread_installments WHERE thread_id=? AND run_id=?", (iran_tid, run_id)
    ).fetchone()[0]
    assert content is not None and "Iran talks resumed" in content

    # The link trace reaches run_artifacts. Asserted on the real wiring, not just on
    # resolve_threads: archive_run_artifacts sweeps claude_input/ BEFORE this runs, so a trace
    # written as a file there would be silently dropped and every unit test would still pass.
    trace = json.loads(
        conn.execute(
            "SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='thread_links.json'", (run_id,)
        ).fetchone()[0]
    )
    assert [s["label"] for s in trace["stories"]] == ["Iran nuclear deal", "EU AI regulation"]
    assert [s["outcome"] for s in trace["stories"]] == ["continued", "new"]
    assert trace["stories"][0]["proposed_thread"] == iran_tid and trace["stories"][0]["refused"] is None
    # the candidates the linker was offered -- without these a mis-link is undiagnosable
    assert [c["thread_id"] for c in trace["candidates"]] == [iran_tid]
    assert trace["linker_ok"] is True


def test_link_trace_survives_a_failure_in_synthesis(staged, monkeypatch):
    """resolve_threads commits identity as it goes, so a synthesis blow-up leaves threads
    written. The trace explaining them must survive it -- a degraded run is the one worth
    auditing, and it was previously discarded by the blanket handler."""
    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)
    monkeypatch.setattr(
        threads,
        "link_threads",
        lambda active, labels, **k: [
            next((t.thread_id for t in active if "Iran" in t.label), None) if "Iran" in lab else None for lab in labels
        ],
    )

    def boom(*a, **k):
        raise RuntimeError("Sonnet 529 overloaded")

    monkeypatch.setattr(thread_synthesis, "synthesize_threads", boom)

    assert run._process_story_threads() == []  # non-fatal, and the trace write itself succeeded

    conn = sqlite3.connect(staged["dbp"])
    row = conn.execute(
        "SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='thread_links.json'", (run_id,)
    ).fetchone()
    assert row is not None, "trace discarded on the exact runs worth auditing"
    trace = json.loads(row[0])
    assert [s["outcome"] for s in trace["stories"]] == ["continued", "new"]


def test_failed_trace_write_is_surfaced_to_the_caller(staged, monkeypatch):
    """fail-soft must not mean silent: _archive_run_and_threads alerts off this list."""
    db.start_run(recording=True, broadcasting=False, alerting=False)
    monkeypatch.setattr(threads, "link_threads", lambda active, labels, **k: [None for _ in labels])
    monkeypatch.setattr(thread_synthesis, "synthesize_threads", lambda *a, **k: ([], 0))
    monkeypatch.setattr(db, "record_run_artifact", lambda *a, **k: False)
    assert run._process_story_threads() == ["thread_links"]


def test_process_story_threads_disabled_is_noop(staged, monkeypatch):
    monkeypatch.setattr(run, "THREADS_ENABLED", False)
    db.start_run(recording=True, broadcasting=False, alerting=False)
    run._process_story_threads()
    assert not (staged["cid"] / "thread_assignments.json").exists()  # gated off -> nothing happens
