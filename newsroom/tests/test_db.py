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


def test_run_usage_persists_duration_ms(fresh_db):
    """The duration_ms migration + record_usage persist per-stage latency (previously logged then
    discarded). A row that omits the key stores NULL rather than crashing (backward-safe)."""
    from usage import usage_row_from_sdk

    rows = [
        usage_row_from_sdk(
            "cluster", "claude-sonnet-4-6", {"input_tokens": 10, "output_tokens": 5}, 0.01, duration_ms=1234
        ),
        # A dict without the duration_ms key (e.g. an older caller) -> NULL, not a KeyError.
        {
            "subagent": "legacy",
            "model": "m",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "api_cost_usd": 0.0,
        },
    ]
    db.record_usage(rows)
    with sqlite3.connect(fresh_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(run_usage)")}
        assert "duration_ms" in cols
        got = dict(conn.execute("SELECT subagent, duration_ms FROM run_usage").fetchall())
    assert got["cluster"] == 1234
    assert got["legacy"] is None


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


def test_archive_run_artifacts_includes_article_csvs(fresh_db, tmp_path):
    # The post-dedup article text (titles + summaries) Claude curated from lives
    # in dynamically-numbered articles_*.csv -- archive every one so the full
    # input, not just the ID index, persists.
    d = tmp_path / "claude_input"
    d.mkdir()
    (d / "articles_1.csv").write_text(
        "article_id,source_id,title,published,summary\nA1,src,Title,2026-06-16,A summary\n"
    )
    (d / "articles_2.csv").write_text("article_id,source_id,title,published,summary\nA2,src,Other,2026-06-16,More\n")
    db.archive_run_artifacts(d)
    arts = db.get_run_artifacts(db._state.run_id)
    assert {"articles_1.csv", "articles_2.csv"} <= set(arts)
    assert "A summary" in arts["articles_1.csv"]


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
    db.complete_run(articles_kept=30, articles_emailed=0)
    assert db.has_completed_run_today() is True


def test_migration_renames_articles_fetched_to_kept(fresh_db):
    # digest_runs.articles_fetched was renamed to articles_kept (it always held the
    # kept count). The old name is gone on digest_runs; source_health keeps its own,
    # genuinely distinct articles_fetched/articles_kept pair.
    with sqlite3.connect(fresh_db) as conn:
        digest_cols = {row[1] for row in conn.execute("PRAGMA table_info(digest_runs)")}
        health_cols = {row[1] for row in conn.execute("PRAGMA table_info(source_health)")}
    assert "articles_kept" in digest_cols
    assert "articles_fetched" not in digest_cols
    assert {"articles_fetched", "articles_kept"} <= health_cols  # source_health untouched


def test_complete_run_writes_articles_kept(fresh_db):
    db.complete_run(articles_kept=42, articles_emailed=7)
    with sqlite3.connect(fresh_db) as conn:
        (kept,) = conn.execute("SELECT articles_kept FROM digest_runs WHERE id = ?", (db._state.run_id,)).fetchone()
    assert kept == 42


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


# --- Failed runs are marked, not deleted (2026-06-16 data-loss regression) ---


def _headline(i: int) -> dict:
    return {
        "headline": f"Headline {i}",
        "tier": "must_know",
        "source_id": "al_jazeera",
        "original_title": f"RSS title {i}",
        "cluster_id": f"cluster-{i}",
    }


def test_abort_run_marks_failed_not_deleted(fresh_db):
    run_id = db._state.run_id
    db.abort_run("RuntimeError('broadcast read timeout')")
    with sqlite3.connect(fresh_db) as conn:
        row = conn.execute("SELECT status, error FROM digest_runs WHERE id = ?", (run_id,)).fetchone()
    assert row is not None, "a failed run must be kept for forensics, not deleted"
    assert row[0] == "failed"
    assert "read timeout" in row[1]


def test_abort_run_resets_state(fresh_db):
    db.abort_run("boom")
    assert db._state.run_id is None
    assert db._state.recording is False


def test_failed_run_is_not_counted_as_completed(fresh_db):
    """The duplicate-run guard / dead-man's switch ignore failed runs."""
    db.abort_run("boom")
    assert db.has_completed_run_today() is False
    assert db.has_completed_run_today(on_error=False) is False


def test_complete_run_sets_status_completed(fresh_db):
    db.complete_run(articles_kept=10, articles_emailed=5)
    with sqlite3.connect(fresh_db) as conn:
        status, completed = conn.execute(
            "SELECT status, completed_at FROM digest_runs WHERE id = ?", (db._state.run_id,)
        ).fetchone()
    assert status == "completed"
    assert completed is not None


@pytest.mark.parametrize("narratives_recorded", [0, 1, 5])
def test_chaos_abort_preserves_run_at_any_progress(fresh_db, narratives_recorded):
    """Invariant: however far a run got before failing, its row survives as
    'failed' and any recorded narratives are kept -- never cascade-deleted.

    This is the exact shape of 2026-06-16: a digest had already recorded
    narratives / been delivered when a later step raised, and the old abort_run
    deleted everything.
    """
    run_id = db._state.run_id
    for i in range(narratives_recorded):
        db.record_shown_headlines([_headline(i)])
    db.abort_run(f"chaos failure after {narratives_recorded} narratives")
    with sqlite3.connect(fresh_db) as conn:
        status = conn.execute("SELECT status FROM digest_runs WHERE id = ?", (run_id,)).fetchone()
        kept = conn.execute("SELECT COUNT(*) FROM shown_narratives WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert status is not None and status[0] == "failed"
    assert kept == narratives_recorded


# --- Broadcast idempotency + delivery state (2026-06-16 double-send guard) ----


def _save_digest(tmp_path, date="2026-06-16"):
    p = tmp_path / f"digest-{date}-1200Z.html"
    p.write_text("<html><body>digest</body></html>")
    db.save_digest(p)
    return date


def test_get_broadcast_none_when_no_digest_row(fresh_db):
    assert db.get_broadcast("2026-06-16") is None


def test_get_broadcast_raises_on_unreadable_db(tmp_path):
    """A DB read error must NOT masquerade as 'nothing sent' (which would invite a
    double-send). It raises so the caller fails closed."""
    db._state = db._State()
    bad = tmp_path / "not-a-db.sqlite"
    bad.write_bytes(b"this is not a sqlite database")
    db._state.db_path = bad
    with pytest.raises(sqlite3.Error):
        db.get_broadcast("2026-06-16")


def test_record_and_get_broadcast(fresh_db, tmp_path):
    date = _save_digest(tmp_path)
    assert db.get_broadcast(date) == (None, None)  # saved but not yet broadcast
    db.record_broadcast(date, "bc_1", "sent")
    assert db.get_broadcast(date) == ("bc_1", "sent")


def test_save_digest_is_idempotent_upsert_by_date(fresh_db, tmp_path):
    """Re-saving the same date overwrites in place -- recovery/resume relies on it."""
    p1 = tmp_path / "digest-2026-06-16-1200Z.html"
    p1.write_text("<html><body>v1</body></html>")
    p2 = tmp_path / "digest-2026-06-16-1300Z.html"
    p2.write_text("<html><body>v2</body></html>")
    db.save_digest(p1)
    db.save_digest(p2)
    with sqlite3.connect(fresh_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM digests WHERE date = '2026-06-16'").fetchone()[0]
        html = conn.execute("SELECT html FROM digests WHERE date = '2026-06-16'").fetchone()[0]
    assert count == 1
    assert "v2" in html


def test_abort_run_noop_when_no_active_run(tmp_path):
    """abort_run with no started run (dry run / --no-record) is a safe no-op."""
    db._state = db._State()
    db.init(tmp_path / "test.db", MIGRATIONS_DIR)
    db.start_run(recording=False)  # returns None, writes no run row
    db.abort_run("must not crash")  # the guard is `run_id is not None`
    assert db._state.run_id is None


def test_record_broadcast_logs_loud_when_no_digest_row(fresh_db, caplog):
    """A 0-row UPDATE means broadcast/idempotency state was silently lost --
    that must be loud, since it feeds straight back into a double-send risk."""
    import logging

    with caplog.at_level(logging.ERROR):
        db.record_broadcast("2099-01-01", "bc_missing", "sent")  # no digests row for that date
    assert any("2099-01-01" in r.getMessage() for r in caplog.records)


def test_digest_date_from_filename_and_fallback():
    from pathlib import Path as _P

    assert db.digest_date(_P("digest-2026-06-16-1200Z.html")) == "2026-06-16"
    # No date in the name -> falls back to today (UTC), never crashes.
    today = __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m-%d")
    assert db.digest_date(_P("digest.html")) == today


def test_record_broadcast_persists_and_reads_recipients(fresh_db, tmp_path):
    date = _save_digest(tmp_path)
    db.record_broadcast(date, "bc_1", "sent", recipients=42)
    assert db.broadcast_recipients(date) == 42


def test_record_broadcast_preserves_recipients_when_none(fresh_db, tmp_path):
    """A later status update (recipients=None) must not wipe the recorded count."""
    date = _save_digest(tmp_path)
    db.record_broadcast(date, "bc_1", "created", recipients=10)
    db.record_broadcast(date, "bc_1", "sent")  # no recipients arg -> preserve
    assert db.broadcast_recipients(date) == 10


def test_broadcast_recipients_zero_when_unknown(fresh_db, tmp_path):
    date = _save_digest(tmp_path)
    db.record_broadcast(date, "bc_1", "created")  # never recorded a count
    assert db.broadcast_recipients(date) == 0
