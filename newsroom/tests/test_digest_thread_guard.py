"""Render-layer guard against the duplicate-cluster_id identical-card bug (run 235).

`digest.attach_thread_context` hands each selection its thread delta by `cluster_id`, and the delta
REPLACES the summary. Two selections that share a `cluster_id` therefore render identically. These
tests pin the guard: detect shared cluster_ids and skip thread enrichment for them so each keeps its
distinct WRITE summary (degrade-not-abort -- the item is never dropped).
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import db
import digest
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


def test_shared_cluster_ids_flags_duplicates_within_tier():
    sels = {
        "must_know": [{"cluster_id": "X"}, {"cluster_id": "X"}, {"cluster_id": "Y"}],
        "should_know": [{"cluster_id": "Z"}],
    }
    assert digest._shared_cluster_ids(sels) == {"X"}


def test_shared_cluster_ids_flags_across_tiers_and_ignores_empty():
    sels = {
        "must_know": [{"cluster_id": "X"}, {"cluster_id": None}, {}],
        "should_know": [{"cluster_id": "X"}],  # cross-tier collision
    }
    assert digest._shared_cluster_ids(sels) == {"X"}
    assert digest._shared_cluster_ids({"must_know": [], "should_know": []}) == set()


@pytest.fixture
def threaded(tmp_path, monkeypatch):
    """Migrated DB + a continuing thread with content, and a claude_input with thread_assignments."""
    cid = tmp_path / "claude_input"
    cid.mkdir()
    dbp = tmp_path / "t.db"
    db._state = db._State()
    db.init(dbp, MIGRATIONS_DIR)
    monkeypatch.setattr(digest, "CLAUDE_INPUT_DIR", cid)
    monkeypatch.setattr(config, "DB_PATH", dbp)
    monkeypatch.setattr(config, "THREADS_ENABLED", True)

    # Seed two continuing threads (prior run 1): the "dup" story (shared by two selections) and a solo.
    seed = sqlite3.connect(dbp)
    seed.execute("INSERT INTO digest_runs (git_sha) VALUES ('r1')")
    seed.commit()
    store = threads.ThreadStore(seed)
    dup_tid = store.create_thread("Iran war", run_id=1)
    store.record_installment(dup_tid, 1, "Iran war", is_new=True)
    solo_tid = store.create_thread("Ukraine reshuffle", run_id=1)
    store.record_installment(solo_tid, 1, "Ukraine reshuffle", is_new=True)
    seed.close()

    (cid / "thread_assignments.json").write_text(
        json.dumps(
            [
                {"thread_id": dup_tid, "is_new": False, "story": "Iran war"},
                {"thread_id": solo_tid, "is_new": False, "story": "Ukraine reshuffle"},
            ]
        )
    )
    # Today's run (so current_run_id resolves).
    db.start_run(recording=True, broadcasting=False, alerting=False)
    return {"cid": cid, "dbp": dbp}


def test_attach_thread_context_skips_enrichment_for_shared_cluster_id(threaded):
    """Two selections sharing a cluster_id get NO thread delta (would render identical); a solo does."""
    selections = {
        "must_know": [
            {"headline": "US-Iran war escalates", "summary": "strike wave", "cluster_id": "Iran war"},
            {"headline": "Iran diplomatic opening", "summary": "Qatar visit", "cluster_id": "Iran war"},
        ],
        "should_know": [
            {"headline": "Zelensky fires minister", "summary": "reshuffle", "cluster_id": "Ukraine reshuffle"},
        ],
    }
    out = digest.attach_thread_context(selections)

    dup_items = out["must_know"]
    assert "thread" not in dup_items[0], "shared cluster_id must not be thread-enriched"
    assert "thread" not in dup_items[1], "shared cluster_id must not be thread-enriched"
    # Distinct summaries survive so the two cards are not identical.
    assert dup_items[0]["summary"] != dup_items[1]["summary"]
    # Control: the solo cluster_id IS enriched (guard is targeted, not blanket-off).
    assert "thread" in out["should_know"][0]
