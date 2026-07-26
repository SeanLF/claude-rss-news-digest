"""One-off repair for duplicate threads left behind by a linker failure.

Run 244 (2026-07-25) shipped with zero continuations: the linker returned its
thread ids as quoted strings and a strict `isinstance(int)` discarded all 16.
`0a86b45` fixed the code so it cannot recur, but the damage is already in the
production database -- five live stories were re-opened as fresh threads, so
their "Ongoing - day N" badges reset to day 1 and the duplicates now hold the
newest labels, which is what the linker matches on next.

`ThreadStore.merge_thread` folds a duplicate back into the thread it should have
continued, and is already tested in test_threads.py. It has no caller. This is
the caller.

The pairs are supplied EXPLICITLY on the command line, never inferred. Guessing
which thread continues which is precisely the judgment that failed here, and a
repair that re-runs the broken inference to fix the broken inference is a second
outage waiting. An operator reads the dry-run plan and decides.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import repair_threads
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def conn(tmp_path):
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def _ensure_runs(conn, run_ids):
    """digest_runs rows for the ids used. threads.*_run_id and
    thread_installments.run_id both have FKs onto digest_runs, and repair_threads
    enables `PRAGMA foreign_keys = ON` (matching db._connect), so a fixture that
    invents run ids is invalid state that production never has."""
    for rid in run_ids:
        conn.execute("INSERT OR IGNORE INTO digest_runs (id, completed_at) VALUES (?, datetime('now'))", (rid,))


def _thread(conn, label, *, runs, questions=()):
    """A thread with one installment per run id in `runs`. first_run_id = runs[0]."""
    _ensure_runs(conn, runs)
    store = threads.ThreadStore(conn)
    tid = store.create_thread(label, run_id=runs[0])
    for i, rid in enumerate(runs):
        store.record_installment(tid, rid, label, is_new=(i == 0))
    if questions:
        store.add_questions(tid, list(questions), run_id=runs[0])
    return tid


def _path(conn) -> str:
    """The file this connection is attached to, so the CLI opens the same DB."""
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _installments(conn, tid):
    return conn.execute("SELECT COUNT(*) FROM thread_installments WHERE thread_id = ?", (tid,)).fetchone()[0]


class TestParsePair:
    def test_parses_source_target(self):
        assert repair_threads.parse_pair("378:356") == (378, 356)

    @pytest.mark.parametrize("bad", ["378", "378:", ":356", "378:356:12", "a:b", "", "378-356"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            repair_threads.parse_pair(bad)

    def test_rejects_self_merge(self):
        """merge_thread raises on this too, but failing at parse time means the
        whole plan is rejected before any pair in the batch is applied."""
        with pytest.raises(ValueError):
            repair_threads.parse_pair("356:356")


class TestPlanIsReadOnly:
    def test_plan_reports_what_would_move(self, conn):
        dup = _thread(conn, "Spain and France wildfires mass evacuation", runs=[244])
        real = _thread(conn, "Cap Ferret wildfire evacuation France 2026", runs=[241, 242, 243])

        rows = repair_threads.plan(conn, [(dup, real)])

        assert len(rows) == 1
        row = rows[0]
        assert row["source_id"] == dup
        assert row["target_id"] == real
        assert row["source_installments"] == 1
        assert row["target_installments"] == 3
        assert row["target_installments_after"] == 4
        assert "Cap Ferret" in row["target_label"]

    def test_plan_writes_nothing(self, conn):
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])

        repair_threads.plan(conn, [(dup, real)])

        assert _installments(conn, dup) == 1
        assert _installments(conn, real) == 2
        assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2

    def test_plan_flags_an_already_merged_pair(self, conn):
        """Idempotency has to be visible in the plan, or a re-run looks like it
        will redo work it will actually skip."""
        real = _thread(conn, "real", runs=[242, 243])
        rows = repair_threads.plan(conn, [(9999, real)])
        assert rows[0]["status"] == "already merged"

    def test_plan_rejects_a_missing_target(self, conn):
        dup = _thread(conn, "dup", runs=[244])
        with pytest.raises(ValueError, match="target"):
            repair_threads.plan(conn, [(dup, 9999)])


class TestReversedPairGuard:
    """Source and target the wrong way round is the plausible operator slip, and it
    survives every other check: the merge succeeds, history is preserved, and the
    surviving thread is the duplicate wearing the real thread's installments.

    The guard compares ORIGIN (first_run_id), not length. Length was the obvious
    proxy and it is wrong in the scenario this tool exists for: the duplicate holds
    the newest label, so later runs link to IT, and a repair done a week after the
    split finds the duplicate legitimately longer than the thread it should have
    continued. Origin is monotone however long the split ran."""

    def test_refuses_when_source_started_earlier(self, conn):
        real = _thread(conn, "real", runs=[240, 241, 242, 243])
        dup = _thread(conn, "dup", runs=[244])

        with pytest.raises(ValueError, match="reversed"):
            repair_threads.plan(conn, [(real, dup)])

    def test_refuses_even_when_the_duplicate_has_grown_longer(self, conn):
        """The compounding case. The split happened at 244; the duplicate has since
        collected five installments and the real thread none, so a length-based
        guard would wave the reversed pair straight through."""
        real = _thread(conn, "real", runs=[240, 241])
        dup = _thread(conn, "dup", runs=[244, 245, 246, 247, 248])

        with pytest.raises(ValueError, match="reversed"):
            repair_threads.plan(conn, [(real, dup)])
        # and the correct direction is still allowed
        assert repair_threads.plan(conn, [(dup, real)])

    def test_force_overrides_the_guard(self, conn):
        real = _thread(conn, "real", runs=[240, 241])
        dup = _thread(conn, "dup", runs=[244])

        rows = repair_threads.plan(conn, [(real, dup)], force=True)
        assert rows[0]["source_id"] == real

    def test_same_origin_is_allowed(self, conn):
        """Both opened on the same run -- ambiguous, not clearly reversed."""
        a = _thread(conn, "a", runs=[244])
        b = _thread(conn, "b", runs=[244])
        assert repair_threads.plan(conn, [(a, b)])


class TestBatchSafety:
    """plan() validates against pre-batch state, but apply_merges mutates between
    pairs. Any id used as both a source and a target, or any source repeated, makes
    the plan a lie -- and in the chained case leaves the batch half-applied and the
    remaining pair permanently un-runnable, because its target no longer exists."""

    def test_rejects_a_chained_batch(self, conn):
        a = _thread(conn, "a", runs=[244])
        b = _thread(conn, "b", runs=[243])
        z = _thread(conn, "z", runs=[240, 241])

        with pytest.raises(ValueError, match="both a source and a target"):
            repair_threads.plan(conn, [(b, z), (a, b)])

    def test_rejects_a_repeated_source(self, conn):
        a = _thread(conn, "a", runs=[244])
        b = _thread(conn, "b", runs=[240, 241])
        z = _thread(conn, "z", runs=[240, 241])

        with pytest.raises(ValueError, match="more than once"):
            repair_threads.plan(conn, [(a, b), (a, z)])

    def test_disjoint_multi_pair_batch_is_fine(self, conn):
        pairs = []
        for i in range(3):
            dup = _thread(conn, f"dup{i}", runs=[244])
            real = _thread(conn, f"real{i}", runs=[241, 242])
            pairs.append((dup, real))

        assert repair_threads.apply_merges(conn, pairs) == 3
        for _, real in pairs:
            assert _installments(conn, real) == 3


class TestPredictedDayCount:
    def test_shared_run_ids_do_not_double_count(self, conn):
        """merge_thread collapses to one installment per run, so a naive
        target+source sum over-predicts whenever the two share a run -- and the
        operator sanity-checks the plan against this number."""
        dup = _thread(conn, "dup", runs=[243, 244])
        real = _thread(conn, "real", runs=[242, 243])

        predicted = repair_threads.plan(conn, [(dup, real)])[0]["target_installments_after"]
        repair_threads.apply_merges(conn, [(dup, real)])

        assert predicted == _installments(conn, real) == 3


class TestApply:
    def test_merges_and_deletes_the_duplicate(self, conn):
        dup = _thread(conn, "dup", runs=[244], questions=["Still open?"])
        real = _thread(conn, "real", runs=[241, 242, 243])

        applied = repair_threads.apply_merges(conn, [(dup, real)])

        assert applied == 1
        assert _installments(conn, real) == 4
        assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (dup,)).fetchone()[0] == 0

    def test_is_idempotent(self, conn):
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])

        repair_threads.apply_merges(conn, [(dup, real)])
        again = repair_threads.apply_merges(conn, [(dup, real)])

        assert again == 0, "a re-run must be a no-op, not a second merge"
        assert _installments(conn, real) == 3


class TestCli:
    def test_dry_run_is_the_default(self, conn, capsys):
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])
        conn.commit()

        assert repair_threads.main([f"--merge={dup}:{real}", f"--db={_path(conn)}"]) == 0

        assert _installments(conn, real) == 2, "no --apply means no writes"
        assert "DRY RUN" in capsys.readouterr().out

    def test_apply_commits(self, conn):
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])
        conn.commit()

        assert repair_threads.main([f"--merge={dup}:{real}", f"--db={_path(conn)}", "--apply"]) == 0
        assert _installments(conn, real) == 3

    def test_requires_at_least_one_pair(self, conn):
        assert repair_threads.main([f"--db={_path(conn)}"]) == 2

    def test_apply_snapshots_the_database_first(self, conn, tmp_path):
        """The delete is irreversible and merge_thread records nothing that could
        undo it, so a wrong-but-plausible pair (a typo'd target that happens to
        exist) is unrecoverable. One VACUUM INTO before the first write is the net."""
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])
        conn.commit()

        assert repair_threads.main([f"--merge={dup}:{real}", f"--db={_path(conn)}", "--apply"]) == 0

        snaps = list(Path(_path(conn)).parent.glob("*.pre-repair-*"))
        assert len(snaps) == 1, f"expected one snapshot, got {snaps}"
        restored = sqlite3.connect(snaps[0])
        assert restored.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (dup,)).fetchone()[0] == 1
        restored.close()

    def test_dry_run_takes_no_snapshot(self, conn):
        dup = _thread(conn, "dup", runs=[244])
        real = _thread(conn, "real", runs=[242, 243])
        conn.commit()

        repair_threads.main([f"--merge={dup}:{real}", f"--db={_path(conn)}"])
        assert not list(Path(_path(conn)).parent.glob("*.pre-repair-*"))
