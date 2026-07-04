"""Hardening pragmas on db._connect().

Guards that every DB connection enforces foreign keys (OFF by default in SQLite,
which would silently ignore the run_id FKs) and waits out a busy writer.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db


def test_connect_applies_hardening_pragmas(tmp_path):
    conn = db._connect(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_foreign_keys_are_actually_enforced(tmp_path):
    """A dangling FK reference must raise -- proves enforcement, not just the pragma."""
    conn = db._connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
        try:
            conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
            conn.commit()
            raise AssertionError("expected a foreign-key violation")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
