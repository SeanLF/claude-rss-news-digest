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
