"""Tests for db.get_run_health: the counts the post-run invariants are judged on.

Uses a real temp SQLite database with migrations applied, so the schema under test
is exactly what production runs.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import run_health

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def fresh_db(tmp_path):
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    run_id = db.start_run(recording=True, broadcasting=False, alerting=False)
    assert run_id is not None
    return db_path


def _thread(conn, *, thread_id, status="active"):
    conn.execute(
        "INSERT INTO threads (id, label, status) VALUES (?, ?, ?)",
        (thread_id, f"thread {thread_id}", status),
    )


def _installment(conn, *, thread_id, run_id, matched_score=None):
    conn.execute(
        "INSERT INTO thread_installments (thread_id, run_id, matched_score) VALUES (?, ?, ?)",
        (thread_id, run_id, matched_score),
    )


class TestThreadCounts:
    def test_a_continuation_is_what_the_linker_matched_not_what_the_data_now_implies(self, fresh_db):
        # Run 244's 16 installments ALL have matched_score NULL -- the linker
        # matched nothing, which was the incident. But bin/repair-threads later
        # merged five of those threads into older ones, so "installment on a thread
        # with an earlier run" now scores that run as 5 continuations and the
        # incident becomes invisible in its own audit trail. matched_score is the
        # linker's own verdict and survives the merge, so it is what we count.
        run_id = db._state.run_id
        with sqlite3.connect(fresh_db) as conn:
            _thread(conn, thread_id=1)
            _installment(conn, thread_id=1, run_id=run_id - 1)
            # A merged-in installment: the thread has an earlier run, but the
            # linker did NOT match it. This is the post-repair run-244 shape.
            _installment(conn, thread_id=1, run_id=run_id, matched_score=None)
            conn.commit()

        health = db.get_run_health(run_id)

        assert health["thread_continuations"] == 0

    def test_linker_matched_installment_counts(self, fresh_db):
        run_id = db._state.run_id
        with sqlite3.connect(fresh_db) as conn:
            _thread(conn, thread_id=1)
            _installment(conn, thread_id=1, run_id=run_id - 1)
            _installment(conn, thread_id=1, run_id=run_id, matched_score=1.0)
            conn.commit()

        assert db.get_run_health(run_id)["thread_continuations"] == 1

    def test_threads_available_counts_only_live_threads(self, fresh_db):
        # "Was there anything to continue" must mean live threads, not every thread
        # that ever existed. An all-time count never returns to zero, so it stops
        # being a guard at all once the system has any history.
        run_id = db._state.run_id
        with sqlite3.connect(fresh_db) as conn:
            _thread(conn, thread_id=1, status="active")
            _installment(conn, thread_id=1, run_id=run_id - 1)
            _thread(conn, thread_id=2, status="closed")
            _installment(conn, thread_id=2, run_id=run_id - 1)
            conn.commit()

        assert db.get_run_health(run_id)["threads_available"] == 1


class TestRecipients:
    def test_unknown_recipient_count_is_not_reported_as_zero(self, fresh_db):
        # The resend-existing-draft recovery path records a broadcast without a
        # count, leaving broadcast_recipients NULL. That digest DID deliver.
        # Collapsing NULL to 0 would alert "sent to nobody" on a successful
        # recovery -- a false outage claim during an incident.
        run_id = db._state.run_id
        with sqlite3.connect(fresh_db) as conn:
            conn.execute(
                "INSERT INTO digests (date, html, run_id, broadcast_recipients) VALUES (?, ?, ?, NULL)",
                ("2026-07-26", "<p>x</p>", run_id),
            )
            conn.commit()

        health = db.get_run_health(run_id)

        assert health["recipients"] is None
        assert not any("ZERO_RECIPIENTS" in v for v in run_health.violations(health))


class TestSchemaDrift:
    def test_health_dict_is_complete_against_the_real_schema(self, fresh_db):
        # Canary. This query reads columns introduced across several migrations; a
        # rename makes get_run_health return {} forever, which the caller treats as
        # "cannot judge" and stays silent about. That looks exactly like a healthy
        # system, so the drift must fail here instead.
        health = db.get_run_health(db._state.run_id)

        assert health, "get_run_health returned {} against a freshly migrated schema"
        assert health.keys() >= run_health.REQUIRED_KEYS
