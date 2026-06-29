"""Tests for the thread-seeding tool (bin/seed-threads -> src/seed_threads.py).

Pins the replay orchestration (a continuing story across two archived runs collapses to one
thread), the run-range selection, the archived-trace requirement, the re-seed guard, and the
articles-CSV parse. The LLM seams are faked, like test_thread_integration.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import seed_threads
import thread_synthesis
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


def _archive(conn, run_id, story, aids, articles):
    conn.execute(
        "INSERT INTO digest_runs (id, git_sha, completed_at) VALUES (?, ?, datetime('now'))",
        (run_id, f"r{run_id}"),
    )
    clusters = {"clusters": [{"story": story, "article_ids": aids}]}
    selected = {"must_know": [{"cluster_index": 0, "article_ids": aids}], "should_know": []}
    csv = "article_id,source_id,title,published,summary\n" + "".join(
        f"{a},src,{t},2026-06-29,{s}\n" for a, (t, s) in articles.items()
    )
    for name, content in (
        ("clusters.json", json.dumps(clusters)),
        ("selected.json", json.dumps(selected)),
        ("articles_1.csv", csv),
    ):
        conn.execute(
            "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)", (run_id, name, content)
        )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path):
    db._state = db._State()
    dbp = tmp_path / "t.db"
    db.init(dbp, MIGRATIONS_DIR)
    conn = sqlite3.connect(dbp)
    _archive(
        conn, 1, "Iran nuclear deal", ["A1", "A2"], {"A1": ("Iran deal", "talks"), "A2": ("Iran Hormuz", "shipping")}
    )
    _archive(
        conn,
        2,
        "Iran nuclear talks resume",
        ["A3", "A4"],
        {"A3": ("Iran Geneva", "talks"), "A4": ("Iran IAEA", "checks")},
    )
    return conn


def _fake_llm(monkeypatch):
    # link: continue the (single) active thread if any, else new -- enough to thread run2 onto run1
    monkeypatch.setattr(
        threads, "link_threads", lambda active, labels, **k: [(active[0].thread_id if active else None) for _ in labels]
    )
    monkeypatch.setattr(
        thread_synthesis,
        "synthesize_installment",
        lambda *a, **k: {"whats_new": [{"fact": "Talks resumed.", "sources": ["A3"]}]},
    )
    monkeypatch.setattr(thread_synthesis, "audit_whats_new", lambda *a, **k: [True])


def test_seed_continues_thread_across_runs(seeded_db, monkeypatch):
    _fake_llm(monkeypatch)
    seed_threads.seed(seeded_db, [1, 2], pace=0)
    assert seeded_db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1  # one thread, not two
    insts = [r[0] for r in seeded_db.execute("SELECT run_id FROM thread_installments ORDER BY run_id")]
    assert insts == [1, 2]  # an installment per run
    content = seeded_db.execute("SELECT content FROM thread_installments WHERE run_id=2").fetchone()[0]
    assert content and "Talks resumed" in content  # continuing run synthesized + persisted


def test_seedable_runs_recent_and_window(seeded_db):
    assert seed_threads.seedable_runs(seeded_db, runs_back=12) == [1, 2]
    assert seed_threads.seedable_runs(seeded_db, runs_back=1) == [2]  # most recent only
    assert seed_threads.seedable_runs(seeded_db, runs_back=0, first=1, last=1) == [1]  # explicit window


def test_seedable_runs_requires_archived_trace(seeded_db):
    seeded_db.execute("INSERT INTO digest_runs (id, git_sha, completed_at) VALUES (3, 'r3', datetime('now'))")
    seeded_db.commit()
    assert 3 not in seed_threads.seedable_runs(seeded_db, runs_back=12)  # no clusters/selected -> not seedable


def test_reset_guard_clears_only_thread_tables(seeded_db, monkeypatch):
    _fake_llm(monkeypatch)
    seed_threads.seed(seeded_db, [1], pace=0)
    assert seed_threads.thread_tables_populated(seeded_db)
    seed_threads.reset_thread_tables(seeded_db)
    assert not seed_threads.thread_tables_populated(seeded_db)
    assert seeded_db.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0] == 2  # run history untouched


def test_articles_from_artifacts_parses_csv_ignores_non_article_files():
    arts = seed_threads._articles_from_artifacts(
        {"articles_1.csv": "article_id,source_id,title,published,summary\nA1,s,T,d,S\n", "clusters.json": "{}"}
    )
    assert arts == {"A1": {"title": "T", "summary": "S"}}
