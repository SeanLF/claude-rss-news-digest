"""Tests for db.get_run_health: the counts the post-run invariants are judged on.

Uses a real temp SQLite database with migrations applied, so the schema under test
is exactly what production runs.
"""

import json
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


class TestClusterHealthArtifactJoin:
    """The archive->SQL join, end to end. Neither side's unit tests touch it: the stage tests
    assert a file on disk, the rule tests hand-build a dict. Four one-token regressions (a
    typo'd JSON path, a wrong artifact name, dropping it from _TRACE_ARTIFACTS, hardcoding the
    count to 0) each kill the feature in production with the suite green.
    """

    def _archive(self, tmp_path, payload):
        (tmp_path / "cluster_health.json").write_text(payload)
        db.archive_run_artifacts(tmp_path)

    def test_a_lost_batch_survives_the_round_trip_and_fires(self, fresh_db, tmp_path):
        self._archive(tmp_path, json.dumps({"articles": 200, "title_only_fallback": 40, "batches_lost": 1}))

        health = db.get_run_health(db._state.run_id)

        assert health["batches_lost"] == 1
        assert any("DEGRADED_CLUSTERING" in v for v in run_health.violations(health))

    def test_a_clean_run_round_trips_as_zero_and_is_silent(self, fresh_db, tmp_path):
        self._archive(tmp_path, json.dumps({"articles": 200, "title_only_fallback": 0, "batches_lost": 0}))

        health = db.get_run_health(db._state.run_id)

        assert health["batches_lost"] == 0
        assert not [v for v in run_health.violations(health) if "DEGRADED_CLUSTERING" in v]

    @pytest.mark.parametrize(
        "payload",
        ['{"batches_lost": 1', "", "not json at all"],
        ids=["truncated", "empty", "prose"],
    )
    def test_a_corrupt_artifact_does_not_blind_every_other_invariant(self, fresh_db, tmp_path, payload):
        # json_extract RAISES on malformed JSON. Caught by get_run_health's blanket
        # `except sqlite3.Error`, that returns {} and the caller skips ALL rules -- so a
        # half-written observability file would silently disable the monitor it was added to
        # feed. A partial write is exactly what an ENOSPC leaves behind.
        self._archive(tmp_path, payload)

        health = db.get_run_health(db._state.run_id)

        assert health, "a corrupt health artifact must not empty the whole health dict"
        assert health["batches_lost"] is None, "unreadable means cannot judge, never clean"
        assert not [v for v in run_health.violations(health) if "DEGRADED_CLUSTERING" in v]

    def test_a_missing_artifact_reads_as_cannot_judge(self, fresh_db, tmp_path):
        db.archive_run_artifacts(tmp_path)  # nothing written

        assert db.get_run_health(db._state.run_id)["batches_lost"] is None


class TestFulltextAndBlankingArtifactJoin:
    """Same archive->SQL join as TestClusterHealthArtifactJoin, for the two rules added after
    runs 280 and 281. A wrong artifact name, a typo'd JSON path, or omitting either file from
    _TRACE_ARTIFACTS leaves the rule reading NULL forever with the suite green -- which is
    precisely how both incidents stayed invisible."""

    def test_a_total_fulltext_loss_survives_the_round_trip_and_fires(self, fresh_db, tmp_path):
        (tmp_path / "fulltext_health.json").write_text(json.dumps({"tasks": 43, "extracted": 0, "outcome": "killed"}))
        db.archive_run_artifacts(tmp_path)

        health = db.get_run_health(db._state.run_id)

        assert health["fulltext_tasks"] == 43
        assert health["fulltext_extracted"] == 0
        assert health["fulltext_outcome"] == "killed"
        assert any("FULLTEXT_TOTAL_LOSS" in v for v in run_health.violations(health))

    def test_the_shape_written_by_the_stage_is_the_shape_the_query_reads(self, fresh_db, tmp_path):
        """Round-trip through the REAL writer, so a key rename in fulltext.py fails here."""
        import fulltext

        fulltext._write_fulltext_health(tmp_path, tasks=40, extracted=31, outcome="completed")
        db.archive_run_artifacts(tmp_path)

        health = db.get_run_health(db._state.run_id)

        assert (health["fulltext_tasks"], health["fulltext_extracted"]) == (40, 31)
        assert health["fulltext_outcome"] == "completed"
        assert not [v for v in run_health.violations(health) if "FULLTEXT_TOTAL_LOSS" in v]

    def test_blanked_why_it_matters_is_counted_from_the_shipped_artifact(self, fresh_db, tmp_path):
        (tmp_path / "selections.json").write_text(
            json.dumps(
                {
                    "must_know": [
                        {"headline": "a", "why_it_matters": ""},
                        {"headline": "b", "why_it_matters": "   "},
                        {"headline": "c", "why_it_matters": "real"},
                    ],
                    "should_know": [{"headline": "d", "why_it_matters": ""}],
                }
            )
        )
        db.archive_run_artifacts(tmp_path)

        health = db.get_run_health(db._state.run_id)

        assert health["blanked_why"] == 3


class TestDroppedContinuations:
    """dropped_continuations is read straight out of thread_links.json rather than a new column:
    the trace already records each refusal, and json_extract over run_artifacts is the pattern
    cluster_health.json established."""

    def _artifact(self, fresh_db, run_id, content):
        with sqlite3.connect(fresh_db) as conn:
            conn.execute(
                "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, 'thread_links.json', ?)",
                (run_id, content),
            )

    def test_counts_only_the_refused_entries(self, fresh_db):
        run_id = db.current_run_id()
        self._artifact(
            fresh_db,
            run_id,
            json.dumps(
                {
                    "linker_ok": True,
                    "stories": [
                        {"refused": "already_claimed"},
                        {"refused": None},
                        {"refused": "already_claimed"},
                        {"refused": "unknown_thread"},
                    ],
                }
            ),
        )
        health = db.get_run_health(run_id)
        assert health["dropped_continuations"] == 2
        assert health["linker_ok"] is True

    def test_absent_artifact_is_none_not_zero(self, fresh_db):
        """Runs archived before the trace existed must read as "cannot judge", or every one of
        them would look like a clean run."""
        health = db.get_run_health(db.current_run_id())
        assert health["dropped_continuations"] is None

    def test_malformed_artifact_does_not_disable_the_other_invariants(self, fresh_db):
        """json_extract RAISES on malformed input, and this function's blanket sqlite3.Error
        handler returns {} -- which would switch off every invariant, not just this one."""
        run_id = db.current_run_id()
        self._artifact(fresh_db, run_id, "{not json at all")
        health = db.get_run_health(run_id)
        assert health != {}, "a half-written trace silently disabled the whole monitor"
        assert health["dropped_continuations"] is None
        assert health["shipped"] is not None

    @pytest.mark.parametrize(
        ("shape", "why"),
        [
            ({"stories": "not an array"}, "container is not an array"),
            ({"other": 1}, "no stories key at all"),
            ({"stories": ["already_claimed"]}, "elements are strings, not objects"),
            ({"stories": [{"refused": "already_claimed"}, "oops"]}, "one element is not an object"),
            ({"stories": [1, 2]}, "elements are numbers"),
            ([1, 2], "top-level array"),
        ],
    )
    def test_a_malformed_trace_reads_as_cannot_judge_and_spares_the_other_invariants(self, fresh_db, shape, why):
        """json_extract RAISES on a non-object, and this function's blanket sqlite3.Error handler
        turns any raise into {} -- which makes the CALLER skip EVERY invariant, not just this one.
        `{"stories": ["already_claimed"]}` clears both json_valid and json_type and still raises,
        so the per-element guard is load-bearing, not belt-and-braces."""
        run_id = db.current_run_id()
        self._artifact(fresh_db, run_id, json.dumps(shape))
        health = db.get_run_health(run_id)
        assert health != {}, f"{why}: a malformed trace disabled the whole monitor"
        assert health["dropped_continuations"] is None, why
        assert health["shipped"] is not None

    def test_an_empty_story_list_is_zero_not_unknown(self, fresh_db):
        run_id = db.current_run_id()
        self._artifact(fresh_db, run_id, json.dumps({"stories": []}))
        assert db.get_run_health(run_id)["dropped_continuations"] == 0
