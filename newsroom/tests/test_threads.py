"""Tests for threads.py -- the evolving story-thread substrate (sub-project A).

The matcher is a Haiku semantic linker (validated on the replay; see
scratch/cluster-replay/thread_linker_haiku.py). Unit tests inject a deterministic fake
linker so the store CRUD, create-or-continue, aging, and resolve_threads orchestration are
covered with no LLM in CI. The link-response parsing + failure fallback are tested directly.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def conn(tmp_path):
    """A migrated DB connection with a few started runs to FK against."""
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    c = sqlite3.connect(db_path)
    for sha in ("r1", "r2", "r3"):
        c.execute("INSERT INTO digest_runs (git_sha) VALUES (?)", (sha,))
    c.commit()
    yield c
    c.close()


def anchor_linker(active, labels):
    """Deterministic stand-in for the Haiku linker: continue a thread when today's label
    shares its leading keyword with the thread's label, else NEW. Lets tests drive
    continuation/separation without an LLM."""

    def key(label):
        return label.lower().split()[0] if label else ""

    out = []
    for lab in labels:
        match = next((t.thread_id for t in active if key(t.label) == key(lab)), None)
        out.append(match)
    return out


# --- migration -------------------------------------------------------------


def test_migration_creates_thread_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"threads", "thread_questions", "thread_installments"} <= tables


# --- link response parsing + failure fallback ------------------------------


def test_parse_links_extracts_array_from_noisy_text():
    text = 'sure!\n{"links": [{"story": 0, "thread": 3}, {"story": 1, "thread": "NEW"}]}\ndone'
    assert threads._parse_links(text) == [
        {"story": 0, "thread": 3},
        {"story": 1, "thread": "NEW"},
    ]


def test_parse_links_returns_empty_on_garbage():
    assert threads._parse_links("no json here") == []


def test_link_threads_no_active_is_all_new():
    assert threads.link_threads([], ["a", "b"]) == [None, None]


def test_link_threads_falls_back_to_all_new_on_llm_error(monkeypatch):
    # Force the (lazy) claude_cli import path to raise -> graceful all-NEW, no crash.
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "claude_cli":
            raise RuntimeError("no SDK in test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    active = [threads.ActiveThread(thread_id=1, label="Iran nuclear deal")]
    assert threads.link_threads(active, ["Iran talks resume"]) == [None]


def test_link_threads_ignores_invalid_thread_ids(monkeypatch):
    active = [threads.ActiveThread(thread_id=7, label="Iran nuclear deal")]
    monkeypatch.setattr(
        threads,
        "_parse_links",
        lambda _t: [{"story": 0, "thread": 999}, {"story": 1, "thread": 7}],
    )
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "{}", raising=False)
    out = threads.link_threads(active, ["unrelated", "Iran update"])
    assert out == [None, 7]  # 999 is not an active id -> dropped to NEW


# --- selected_labels (SELECT + CLUSTER -> story list) ----------------------


def test_selected_labels_maps_cluster_index_in_tier_order():
    clusters = {
        "clusters": [
            {"story": "Iran deal", "article_ids": ["A1", "A2"]},
            {"story": "Trump tariffs", "article_ids": ["A3"]},
            {"story": "Heatwave", "article_ids": ["A4"]},
        ]
    }
    selected = {
        "must_know": [{"cluster_index": 0, "article_ids": ["A1"]}],
        "should_know": [{"cluster_index": 2}],
    }
    assert threads.selected_labels(clusters, selected) == [
        {"story": "Iran deal", "tier": "must_know", "article_ids": ["A1"]},  # selected subset wins
        {"story": "Heatwave", "tier": "should_know", "article_ids": ["A4"]},  # falls back to cluster ids
    ]


def test_selected_labels_skips_out_of_range_index():
    clusters = {"clusters": [{"story": "only one", "article_ids": ["A1"]}]}
    selected = {"must_know": [{"cluster_index": 5}, {"cluster_index": 0}]}
    assert threads.selected_labels(clusters, selected) == [
        {"story": "only one", "tier": "must_know", "article_ids": ["A1"]}
    ]


# --- store + resolve_threads ----------------------------------------------


def test_resolve_threads_creates_new_threads_first_run(conn):
    store = threads.ThreadStore(conn)
    stories = [
        {"story": "Iran ceasefire over Strait of Hormuz"},
        {"story": "Trump tariff threat on China imports"},
    ]
    out = threads.resolve_threads(stories, run_id=1, store=store, linker=anchor_linker)
    assert len(out) == 2
    assert all(a.is_new for a in out)
    assert len({a.thread_id for a in out}) == 2
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2


def test_resolve_threads_continues_thread_next_run(conn):
    store = threads.ThreadStore(conn)
    a1 = threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    tid = a1[0].thread_id
    a2 = threads.resolve_threads(
        [{"story": "Iran ceasefire holds as shipping resumes"}], run_id=2, store=store, linker=anchor_linker
    )
    assert a2[0].is_new is False
    assert a2[0].thread_id == tid
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM thread_installments WHERE thread_id=?", (tid,)).fetchone()[0] == 2
    # the thread's label advanced to the latest installment
    assert conn.execute("SELECT label FROM threads WHERE id=?", (tid,)).fetchone()[0].startswith("Iran ceasefire holds")


def test_resolve_threads_distinct_story_starts_new_thread(conn):
    store = threads.ThreadStore(conn)
    threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    out = threads.resolve_threads(
        [{"story": "EU agrees landmark AI regulation package"}], run_id=2, store=store, linker=anchor_linker
    )
    assert out[0].is_new is True
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2


def test_resolve_threads_carries_prior_narrative_and_questions(conn):
    store = threads.ThreadStore(conn)
    a1 = threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    tid = a1[0].thread_id
    # B will write these; simulate via the store API.
    store.set_narrative(tid, "Iran and the US reached a ceasefire after strikes near Hormuz.")
    store.add_questions(tid, ["Will the ceasefire hold?"], run_id=1)

    a2 = threads.resolve_threads(
        [{"story": "Iran ceasefire holds as shipping resumes"}], run_id=2, store=store, linker=anchor_linker
    )
    assert "ceasefire" in (a2[0].prior_narrative or "")
    assert "Will the ceasefire hold?" in a2[0].open_questions


def test_active_threads_prefers_narrative_then_label(conn):
    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran nuclear talks", run_id=1)
    active = store.active_threads(before_run_id=2, dormant_after=3)
    assert active[0].label == "Iran nuclear talks"
    assert active[0].narrative is None
    store.set_narrative(tid, "running summary")
    assert store.active_threads(before_run_id=2, dormant_after=3)[0].narrative == "running summary"


def test_set_narrative_rolls_current_into_prev(conn):
    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran", run_id=1)
    store.set_narrative(tid, "day-2 state")
    store.set_narrative(tid, "day-3 state")
    row = conn.execute("SELECT narrative, prev_narrative FROM threads WHERE id=?", (tid,)).fetchone()
    assert row == ("day-3 state", "day-2 state")


def test_render_context_returns_prior_narrative_as_story_so_far(conn):
    # The renderer should see the PRIOR run's narrative (background), not today's update.
    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran", run_id=1)
    store.record_installment(tid, 1, "Iran", is_new=True)
    store.record_installment(tid, 2, "Iran", is_new=False)
    store.set_narrative(tid, "as of day 1")
    store.set_narrative(tid, "as of day 2 (includes today)")
    ctx = store.render_context(tid)
    assert ctx["day"] == 2
    assert ctx["narrative"] == "as of day 1"  # prior, not the current today-laden narrative


def test_resolve_threads_ages_out_dormant_threads(conn):
    store = threads.ThreadStore(conn)
    threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    # Five COMPLETED runs pass with the story absent (> dormant_after=3).
    for rid in range(4, 9):
        conn.execute(
            "INSERT INTO digest_runs (id, git_sha, completed_at) VALUES (?, ?, datetime('now', 'utc'))",
            (rid, f"r{rid}"),
        )
    conn.commit()
    out = threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}],
        run_id=9,
        store=store,
        dormant_after=3,
        linker=anchor_linker,
    )
    assert out[0].is_new is True  # old thread is dormant -> a fresh one starts
    assert "dormant" in {r[0] for r in conn.execute("SELECT status FROM threads")}


def test_dormancy_counts_completed_runs_not_run_id_gaps(conn):
    # A large run-id gap made entirely of FAILED runs (completed_at NULL) must NOT retire a
    # thread -- regression guard for the failed-run-gap issue (cf. the 2026-06-16 incident).
    store = threads.ThreadStore(conn)
    threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    for rid in range(4, 9):  # five FAILED runs (no completed_at)
        conn.execute("INSERT INTO digest_runs (id, git_sha) VALUES (?, ?)", (rid, f"f{rid}"))
    conn.commit()
    out = threads.resolve_threads(
        [{"story": "Iran ceasefire holds"}], run_id=9, store=store, dormant_after=3, linker=anchor_linker
    )
    assert out[0].is_new is False  # thread survived the failed-run gap
    assert "dormant" not in {r[0] for r in conn.execute("SELECT status FROM threads")}


def test_resolve_threads_does_not_collapse_two_stories_into_one_thread(conn):
    # The linker may map two of today's stories to the same thread id; the second must NOT
    # silently overwrite the first -- it starts a fresh thread instead.
    store = threads.ThreadStore(conn)
    a1 = threads.resolve_threads([{"story": "Iran nuclear deal talks"}], run_id=1, store=store, linker=anchor_linker)
    tid = a1[0].thread_id
    out = threads.resolve_threads(
        [{"story": "Iran talks continue"}, {"story": "Iran strikes begin"}],
        run_id=2,
        store=store,
        linker=lambda active, labels: [tid, tid],
    )
    assert out[0].thread_id == tid and out[0].is_new is False
    assert out[1].is_new is True and out[1].thread_id != tid
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2
    # exactly one installment for the continued thread this run (no duplicate)
    assert (
        conn.execute("SELECT COUNT(*) FROM thread_installments WHERE thread_id=? AND run_id=2", (tid,)).fetchone()[0]
        == 1
    )


def test_resolve_threads_handles_empty_story(conn):
    store = threads.ThreadStore(conn)
    out = threads.resolve_threads([{"story": ""}], run_id=1, store=store, linker=anchor_linker)
    assert len(out) == 1 and out[0].is_new is True
