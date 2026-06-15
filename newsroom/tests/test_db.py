"""Tests for db.py cluster persistence (shown_narratives.cluster_id, cluster_runs).

Uses a real temp SQLite database with migrations applied via db.init(), so the
schema under test is exactly what production runs.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Initialise a temp DB with migrations applied and a started run."""
    # Reset module-level state so tests don't leak into each other.
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)
    assert run_id is not None
    return db_path


def test_migration_adds_cluster_id_column(fresh_db):
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(shown_narratives)")}
    assert "cluster_id" in cols


def test_migration_creates_cluster_runs_table(fresh_db):
    with sqlite3.connect(fresh_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "cluster_runs" in tables


def test_record_shown_headlines_stores_cluster_id(fresh_db):
    db.record_shown_headlines(
        [
            {
                "headline": "Iran fires missiles",
                "tier": "must_know",
                "source_id": "al_jazeera",
                "original_title": "Iran strikes Gulf",
                "cluster_id": "Iran Missile Attacks on Gulf States",
            }
        ]
    )
    with sqlite3.connect(fresh_db) as conn:
        row = conn.execute("SELECT headline, cluster_id FROM shown_narratives").fetchone()
    assert row == ("Iran fires missiles", "Iran Missile Attacks on Gulf States")


def test_record_shown_headlines_cluster_id_nullable(fresh_db):
    # Rows without cluster_id (e.g. unmapped) persist with NULL, not a crash.
    db.record_shown_headlines([{"headline": "h", "tier": "must_know", "source_id": "x"}])
    with sqlite3.connect(fresh_db) as conn:
        row = conn.execute("SELECT cluster_id FROM shown_narratives").fetchone()
    assert row[0] is None


def test_archive_clusters_persists_blob(fresh_db):
    payload = {"clusters": [{"story": "A", "article_ids": ["A1", "A2"]}]}
    db.archive_clusters(json.dumps(payload))
    with sqlite3.connect(fresh_db) as conn:
        row = conn.execute("SELECT run_id, clusters_json FROM cluster_runs").fetchone()
    assert row[0] == db._state.run_id
    assert json.loads(row[1]) == payload


def test_archive_clusters_noop_when_not_recording(tmp_path):
    db._state = db._State()
    db.init(tmp_path / "test.db", MIGRATIONS_DIR)
    db.start_run(recording=False)
    db.archive_clusters('{"clusters": []}')
    # No run/recording -> nothing written, and no exception.
    with sqlite3.connect(tmp_path / "test.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM cluster_runs").fetchone()[0]
    assert count == 0


def test_delete_run_removes_cluster_runs(fresh_db):
    run_id = db._state.run_id
    db.archive_clusters('{"clusters": []}')
    db.delete_run(run_id)
    with sqlite3.connect(fresh_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM cluster_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert count == 0


# --- run_artifacts (durable per-run trace store) ---------------------------


def _write_claude_input(tmp_path):
    """Build a claude_input dir with a representative subset of trace files."""
    d = tmp_path / "claude_input"
    d.mkdir()
    (d / "clusters.json").write_text('{"clusters": [{"story": "A"}]}')
    (d / "selected.json").write_text('{"selected": ["A1"]}')
    (d / "recap.txt").write_text("recent titles recap")
    return d


def test_migration_creates_run_artifacts_table(fresh_db):
    with sqlite3.connect(fresh_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "run_artifacts" in tables


def test_archive_run_artifacts_persists_existing_files(fresh_db, tmp_path):
    d = _write_claude_input(tmp_path)
    db.archive_run_artifacts(d)
    arts = db.get_run_artifacts(db._state.run_id)
    # Only files that exist are stored; missing ones (e.g. coherence_report.json) skipped.
    assert set(arts) == {"clusters.json", "selected.json", "recap.txt"}
    assert arts["recap.txt"] == "recent titles recap"
    assert json.loads(arts["clusters.json"]) == {"clusters": [{"story": "A"}]}


def test_archive_run_artifacts_stores_models(fresh_db, tmp_path):
    d = _write_claude_input(tmp_path)
    db.archive_run_artifacts(d, models={"select": "claude-sonnet-4-6"})
    arts = db.get_run_artifacts(db._state.run_id)
    assert json.loads(arts["models.json"]) == {"select": "claude-sonnet-4-6"}


def test_archive_run_artifacts_noop_when_not_recording(tmp_path):
    db._state = db._State()
    db.init(tmp_path / "test.db", MIGRATIONS_DIR)
    db.start_run(recording=False)
    db.archive_run_artifacts(_write_claude_input(tmp_path))
    with sqlite3.connect(tmp_path / "test.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM run_artifacts").fetchone()[0]
    assert count == 0


def test_archive_run_artifacts_empty_dir_is_soft(fresh_db, tmp_path):
    empty = tmp_path / "claude_input"
    empty.mkdir()
    db.archive_run_artifacts(empty)  # no files, no models -> no rows, no crash
    assert db.get_run_artifacts(db._state.run_id) == {}


def test_delete_run_removes_run_artifacts(fresh_db, tmp_path):
    run_id = db._state.run_id
    db.archive_run_artifacts(_write_claude_input(tmp_path))
    db.delete_run(run_id)
    with sqlite3.connect(fresh_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM run_artifacts WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert count == 0


def test_has_completed_run_today_false_before_complete(fresh_db):
    # fresh_db starts a run but does not complete it.
    assert db.has_completed_run_today() is False


def test_has_completed_run_today_true_after_complete(fresh_db):
    db.complete_run(articles_fetched=30, articles_emailed=0)
    assert db.has_completed_run_today() is True


def test_has_completed_run_today_on_error_controls_failopen(tmp_path, monkeypatch):
    # Point at a file that exists but is not a valid SQLite DB so the query raises.
    db._state = db._State()
    bad = tmp_path / "not-a-db.sqlite"
    bad.write_bytes(b"this is not a sqlite database")
    db._state.db_path = bad
    # Duplicate-run guard fails closed (assume a run exists -> do not double-run).
    assert db.has_completed_run_today() is True
    assert db.has_completed_run_today(on_error=True) is True
    # Dead-man's switch fails open (assume no run -> alert).
    assert db.has_completed_run_today(on_error=False) is False
