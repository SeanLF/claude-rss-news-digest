"""Tests for the one-off backfill of thread labels harvested from the wrong cluster.

Labels used to come from SELECT's `cluster_index`, a position a model counts by eye into a
several-hundred-element array. It is wrong ~16% of the time, so 110 of 668 archived installments
are published under a label describing a different story from the same run (`b114c6a` fixed it
forward by deriving the label from `article_ids`; this backfills what shipped before that).

The tool corrects only installments that STARTED a thread. Where the linker CONTINUED a thread it
matched on the wrong label, so the installment is in the wrong thread as well as under the wrong
name, and relabelling alone would splice a visibly unrelated story into an existing arc.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import relabel_installments as relabel

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"

CLUSTERS = {
    "clusters": [
        {"story": "Kyiv strikes", "article_ids": ["A1", "A2"]},
        {"story": "EU AI act", "article_ids": ["A3"]},
        {"story": "Chip tooling", "article_ids": ["A4"]},
    ]
}
# SELECT picked the Kyiv and Chip clusters. cluster_index is deliberately wrong on the second
# entry -- the shape the backfill exists to undo -- while article_ids stay correct.
SELECTED = {
    "must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}],
    "should_know": [{"cluster_index": 1, "article_ids": ["A4"]}],
}


@pytest.fixture
def conn(tmp_path):
    db._state = db._State()
    db_path = tmp_path / "t.db"
    db.init(db_path, MIGRATIONS_DIR)
    c = sqlite3.connect(db_path)
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("INSERT INTO digest_runs (git_sha) VALUES ('r1')")
    for name, doc in (("clusters.json", CLUSTERS), ("selected.json", SELECTED)):
        c.execute(
            "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (1, ?, ?)",
            (name, json.dumps(doc)),
        )
    c.commit()
    return c


def _seed(conn, *installments):
    """installments: (thread_id, label, matched_score). Thread label = its last installment."""
    for tid, label, score in installments:
        conn.execute(
            "INSERT OR IGNORE INTO threads (id, slug, label, status, first_run_id, last_run_id) "
            "VALUES (?, ?, ?, 'active', 1, 1)",
            (tid, f"t{tid}", label),
        )
        conn.execute("UPDATE threads SET label = ? WHERE id = ?", (label, tid))
        conn.execute(
            "INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) VALUES (?, 1, ?, ?)",
            (tid, label, score),
        )
    conn.commit()


def test_plan_names_the_wrong_label_and_its_correction(conn):
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    fix, _ = relabel.plan(conn)
    assert len(fix) == 1
    assert fix[0]["stored"] == "EU AI act"
    assert fix[0]["correct"] == "Chip tooling"
    assert fix[0]["thread_id"] == 2


def test_a_correct_label_is_left_alone(conn):
    _seed(conn, (1, "Kyiv strikes", None), (2, "Chip tooling", None))
    fix, _ = relabel.plan(conn)
    assert fix == []


def test_a_continuation_is_refused_not_relabelled(conn):
    """The linker matched on the wrong label, so the installment is in the wrong THREAD too.
    Relabelling alone would splice an unrelated story into an existing arc."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", 1.0))
    fix, skipped = relabel.plan(conn)
    assert fix == []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "continuation"
    assert skipped[0]["thread_id"] == 2


def test_dry_run_writes_nothing(conn):
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    relabel.plan(conn)
    assert conn.execute("SELECT cluster_story FROM thread_installments WHERE thread_id=2").fetchone()[0] == "EU AI act"


def test_apply_corrects_the_installment_and_the_thread_label(conn):
    """The thread's own label is what /thread/{id} shows and what the linker matches on next
    run, so leaving it stale would keep the error reader-visible and self-perpetuating."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    fix, _ = relabel.plan(conn)
    assert relabel.apply_fixes(conn, fix) == 1
    assert (
        conn.execute("SELECT cluster_story FROM thread_installments WHERE thread_id=2").fetchone()[0] == "Chip tooling"
    )
    assert conn.execute("SELECT label FROM threads WHERE id=2").fetchone()[0] == "Chip tooling"


def test_a_thread_label_from_a_later_run_is_not_clobbered(conn):
    """Only the thread whose CURRENT label is the wrong one gets it rewritten; a thread that has
    since moved on carries a label this backfill has no opinion about."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    conn.execute("UPDATE threads SET label = 'a later story' WHERE id = 2")
    conn.commit()
    fix, _ = relabel.plan(conn)
    relabel.apply_fixes(conn, fix)
    assert conn.execute("SELECT label FROM threads WHERE id=2").fetchone()[0] == "a later story"
    assert (
        conn.execute("SELECT cluster_story FROM thread_installments WHERE thread_id=2").fetchone()[0] == "Chip tooling"
    )


def test_applying_twice_is_a_no_op(conn):
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    fix, _ = relabel.plan(conn)
    relabel.apply_fixes(conn, fix)
    again, _ = relabel.plan(conn)
    assert again == []


def test_a_run_whose_entries_cannot_be_aligned_is_skipped_whole(conn):
    """The correction is positional: the Nth installment of a run is the Nth selected entry. If
    the counts disagree that mapping is unsound, and guessing would relabel by coincidence."""
    _seed(conn, (1, "Kyiv strikes", None))  # 1 installment, 2 selected entries
    fix, skipped = relabel.plan(conn)
    assert fix == []
    assert any(s["reason"] == "unalignable_run" for s in skipped)


def test_a_run_with_no_artifacts_is_skipped(conn):
    conn.execute("DELETE FROM run_artifacts WHERE artifact_name = 'clusters.json'")
    conn.commit()
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    fix, skipped = relabel.plan(conn)
    assert fix == []
    assert any(s["reason"] == "no_artifacts" for s in skipped)


# --- the shapes that corrupted the archive on the first attempt -----------------
#
# Each of these passed the original `matched_score IS NULL` / length-equality version and wrote a
# wrong label onto a published arc. They are the regression tests for that.


def _run2(conn, clusters, selected):
    conn.execute("INSERT INTO digest_runs (git_sha) VALUES ('r2')")
    for name, doc in (("clusters.json", clusters), ("selected.json", selected)):
        conn.execute(
            "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (2, ?, ?)",
            (name, json.dumps(doc)),
        )
    conn.commit()


def test_the_head_of_a_continued_thread_is_refused(conn):
    """Thread 196 in production: an 11-installment Ebola arc whose head was relabelled to bird
    flu. The rows after it linked BECAUSE of the Ebola label, so rewriting the head alone opens
    the arc on an unrelated story -- the same splice a continuation would cause."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    _run2(conn, CLUSTERS, {"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}], "should_know": []})
    conn.execute(
        "INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) "
        "VALUES (2, 2, 'EU AI act continued', 1.0)"
    )
    conn.commit()
    fix, skipped = relabel.plan(conn)
    assert fix == []
    assert any(s.get("reason") == "head_of_continued_thread" and s["thread_id"] == 2 for s in skipped)


def test_a_merged_in_installment_is_refused(conn):
    """merge_thread moves installments between threads with an UPDATE that never touches
    matched_score, so a NULL score means "started SOME thread", possibly one since merged away.
    Thread 261 in production: a 10-day Ukraine arc whose public label was rewritten to an
    unrelated ICC headline because a merged-in row still read as NULL."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    _run2(conn, CLUSTERS, {"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}], "should_know": []})
    # an older installment merged into thread 2 from a thread that no longer exists
    conn.execute(
        "INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) "
        "VALUES (2, 2, 'the real arc', NULL)"
    )
    conn.commit()
    fix, _ = relabel.plan(conn)
    assert fix == [], "a NULL matched_score on a thread with an arc is not proof it started it"


def test_a_run_whose_positions_cannot_be_proven_is_skipped(conn):
    """Equal counts are not alignment. The old and new derivations skip DIFFERENT entries, so a
    run can match on length and diverge at every position; a length-only guard relabels by
    coincidence and exits 0."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    # Stored labels no longer match what the old derivation produces at these positions.
    conn.execute("UPDATE thread_installments SET cluster_story = 'Chip tooling' WHERE thread_id = 1")
    conn.commit()
    fix, skipped = relabel.plan(conn)
    assert fix == []
    assert any(s.get("reason") == "alignment_unproven" for s in skipped)


def test_a_second_run_is_not_blinded_by_the_first(conn):
    """The alignment proof compares stored labels to the OLD derivation, which this tool's own
    writes invalidate. Without also accepting the corrected value, one --apply blinds it: every
    later run reports "0 to relabel" beside a pile of skipped runs, which reads exactly like
    "nothing left to do" while the refused list silently vanishes."""
    _seed(conn, (1, "Kyiv strikes", None), (2, "EU AI act", None))
    fix, _ = relabel.plan(conn)
    assert relabel.apply_fixes(conn, fix) == 1

    again, skipped = relabel.plan(conn)
    assert again == []
    assert not any(s.get("reason") == "alignment_unproven" for s in skipped), "the tool blinded itself"
