"""Tests for thread_synthesis.py -- threaded synthesis + ledger (sub-project B).

The LLM calls (synthesize_installment, audit_whats_new) are injected as fakes so the
persistence logic (audit-drop, narrative update, question resolution) and orchestration run
with no model in CI. Parsing + bundling are tested directly.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import thread_synthesis as ts
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"

ARTS = {
    "A1": {"title": "Iran and US sign ceasefire", "summary": "A ceasefire was signed in Geneva."},
    "A2": {"title": "Shipping resumes in Hormuz", "summary": "Tankers transit the strait again."},
}


@pytest.fixture
def store(tmp_path):
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    c = sqlite3.connect(db_path)
    for sha in ("r1", "r2"):
        c.execute("INSERT INTO digest_runs (git_sha) VALUES (?)", (sha,))
    c.commit()
    return threads.ThreadStore(c)


# --- pure helpers ----------------------------------------------------------


def test_parse_json_ignores_trailing_prose():
    assert ts._parse_json('{"whats_new": []}\nthat is all') == {"whats_new": []}


def test_bundle_formats_cited_articles_only():
    out = ts._bundle(["A1", "A9"], ARTS)
    assert "A1: Iran and US sign ceasefire" in out
    assert "A9" not in out  # missing article skipped, no crash


# --- apply_installment -----------------------------------------------------


def _seed_thread(store, *, open_qs=()):
    tid = store.create_thread("Iran ceasefire", run_id=1)
    store.record_installment(tid, 2, "Iran ceasefire holds", is_new=False)  # B updates this run-2 row
    if open_qs:
        store.add_questions(tid, list(open_qs), run_id=1)
    return tid


def test_apply_installment_drops_unsupported_facts(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="Iran ceasefire holds")
    installment = {
        "whats_new": [
            {"fact": "Ceasefire signed", "sources": ["A1"]},
            {"fact": "Fabricated casualty figure", "sources": ["A1"]},
        ],
        "updated_narrative": "Iran and the US signed a ceasefire.",
    }
    verified = ts.apply_installment(store, a, installment, supported=[True, False], run_id=2)
    assert [f["fact"] for f in verified["whats_new"]] == ["Ceasefire signed"]
    # persisted installment content matches the verified (dropped) set
    row = store.conn.execute(
        "SELECT content FROM thread_installments WHERE thread_id=? AND run_id=2", (tid,)
    ).fetchone()
    assert len(json.loads(row[0])["whats_new"]) == 1


def test_apply_installment_updates_narrative(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="x")
    ts.apply_installment(store, a, {"updated_narrative": "New running summary."}, supported=[], run_id=2)
    assert (
        store.conn.execute("SELECT narrative FROM threads WHERE id=?", (tid,)).fetchone()[0] == "New running summary."
    )


def test_apply_installment_resolves_only_carried_questions(store):
    tid = _seed_thread(store, open_qs=["Will the ceasefire hold?"])
    a = threads.ThreadAssignment(
        thread_id=tid, is_new=False, cluster_story="x", open_questions=["Will the ceasefire hold?"]
    )
    installment = {
        "resolved": [
            {"question": "Will the ceasefire hold?", "how": "Both sides reaffirmed it."},
            {"question": "A question never asked", "how": "ignored"},  # not carried -> not resolved
        ],
        "new_questions": ["Who monitors compliance?"],
    }
    ts.apply_installment(store, a, installment, supported=[], run_id=2)
    rows = dict(
        store.conn.execute("SELECT question, status FROM thread_questions WHERE thread_id=?", (tid,)).fetchall()
    )
    assert rows["Will the ceasefire hold?"] == "resolved"
    assert rows["Who monitors compliance?"] == "open"
    assert "A question never asked" not in rows  # phantom resolve created nothing


# --- synthesize_threads orchestration --------------------------------------


def test_synthesize_threads_skips_new_and_thin_threads(store):
    new = threads.ThreadAssignment(thread_id=1, is_new=True, cluster_story="new", article_ids=["A1", "A2"])
    thin = threads.ThreadAssignment(thread_id=2, is_new=False, cluster_story="thin", article_ids=["A1"])
    called = []
    ts.synthesize_threads(
        [new, thin],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *a, **k: called.append(1) or {"whats_new": []},
        audit_fn=lambda *a, **k: [],
    )
    assert called == []  # neither qualifies (new / too few articles)


def test_synthesize_threads_synthesizes_continuing_thread(store):
    tid = _seed_thread(store, open_qs=["Will the ceasefire hold?"])
    a = threads.ThreadAssignment(
        thread_id=tid,
        is_new=False,
        cluster_story="Iran ceasefire holds",
        article_ids=["A1", "A2"],
        open_questions=["Will the ceasefire hold?"],
    )
    fake_installment = {
        "whats_new": [{"fact": "Ceasefire signed", "sources": ["A1"]}, {"fact": "bad", "sources": ["A1"]}],
        "resolved": [{"question": "Will the ceasefire hold?", "how": "reaffirmed"}],
        "new_questions": ["Who monitors it?"],
        "updated_narrative": "A ceasefire holds.",
    }
    out, _ = ts.synthesize_threads(
        [a],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *args, **k: fake_installment,
        audit_fn=lambda *args, **k: [True, False],  # drop the second fact
    )
    assert len(out) == 1
    assert [f["fact"] for f in out[0]["whats_new"]] == ["Ceasefire signed"]
    assert out[0]["thread_id"] == tid
    # ledger updated
    rows = dict(
        store.conn.execute("SELECT question, status FROM thread_questions WHERE thread_id=?", (tid,)).fetchall()
    )
    assert rows["Will the ceasefire hold?"] == "resolved"


def test_synthesize_threads_skips_failing_thread_without_crashing(store):
    tid = _seed_thread(store)
    good = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])

    def boom(*a, **k):
        raise RuntimeError("synthesis exploded")

    out, _ = ts.synthesize_threads([good], ARTS, run_id=2, store=store, synth_fn=boom, audit_fn=lambda *a, **k: [])
    assert out == []  # failure swallowed, no exception propagated


def test_audit_whats_new_empty_is_empty():
    assert ts.audit_whats_new([], ARTS) == []


def test_audit_whats_new_raises_on_llm_error(monkeypatch):
    # audit now RAISES on LLM/parse failure; synthesize_threads owns the fail-open + counting.
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    wn = [{"fact": "x", "sources": ["A1"]}]
    with pytest.raises(RuntimeError):
        ts.audit_whats_new(wn, ARTS)


def test_synthesize_threads_audit_failure_is_fail_open_and_recorded(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])
    fake = {"whats_new": [{"fact": "kept despite audit error", "sources": ["A1"]}], "updated_narrative": "n"}

    def boom_audit(*a, **k):
        raise RuntimeError("audit endpoint down")

    out, failures = ts.synthesize_threads(
        [a], ARTS, run_id=2, store=store, synth_fn=lambda *args, **k: fake, audit_fn=boom_audit
    )
    assert failures == 1  # the authoritative in-memory count (drives the alert)
    # fail-open: the fact is kept (not erased) despite the audit error
    assert [f["fact"] for f in out[0]["whats_new"]] == ["kept despite audit error"]
    # ...and the failure is also recorded as durable health history
    row = store.conn.execute("SELECT threads_synthesized, audit_failures FROM thread_runs WHERE run_id=2").fetchone()
    assert row == (1, 1)


def test_synthesize_threads_threads_usage_rows_through(store):
    # usage_rows must reach both the synth and audit calls so B's spend lands in run_usage.
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])

    def synth(*args, usage_rows=None, **k):
        usage_rows.append({"subagent": "thread_synthesis"})
        return {"whats_new": [{"fact": "x", "sources": ["A1"]}], "updated_narrative": "n"}

    def audit(*args, usage_rows=None, **k):
        usage_rows.append({"subagent": "thread_audit"})
        return [True]

    rows: list = []
    ts.synthesize_threads([a], ARTS, run_id=2, store=store, synth_fn=synth, audit_fn=audit, usage_rows=rows)
    assert [r["subagent"] for r in rows] == ["thread_synthesis", "thread_audit"]


def test_synthesize_threads_records_zero_failures_on_clean_run(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])
    ts.synthesize_threads(
        [a],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *args, **k: {"whats_new": [], "updated_narrative": "n"},
        audit_fn=lambda *a, **k: [],
    )
    assert store.conn.execute("SELECT audit_failures FROM thread_runs WHERE run_id=2").fetchone()[0] == 0


def test_apply_installment_is_atomic_on_persist_failure(store, monkeypatch):
    # If a late write fails, the narrative must NOT have advanced (the whole unit rolls back).
    tid = _seed_thread(store)
    store.set_narrative(tid, "OLD narrative")
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="x")
    monkeypatch.setattr(
        store, "set_installment_content", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    with pytest.raises(RuntimeError):
        ts.apply_installment(store, a, {"updated_narrative": "NEW narrative"}, supported=[], run_id=2)
    assert store.conn.execute("SELECT narrative FROM threads WHERE id=?", (tid,)).fetchone()[0] == "OLD narrative"
