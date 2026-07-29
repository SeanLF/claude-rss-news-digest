"""Tests for threads.py -- the evolving story-thread substrate (sub-project A).

The matcher is a Haiku semantic linker (validated on the replay; see
scratch/cluster-replay/thread_linker_haiku.py). Unit tests inject a deterministic fake
linker so the store CRUD, create-or-continue, aging, and resolve_threads orchestration are
covered with no LLM in CI. The link-response parsing + failure fallback are tested directly.
"""

import logging
import re
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
    # FKs ON to match prod (db.py). With them off, a merge that orphaned installment rows
    # would pass here and fail in production; it also means a test referencing run N must
    # insert run N first (the dormancy tests below do exactly that, with controlled
    # completed_at values, which is why this seeds only the base three).
    c.execute("PRAGMA foreign_keys = ON")
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


def test_link_threads_accepts_string_thread_ids(monkeypatch):
    """Run 244 (2026-07-25) regression: Haiku quoted every id ({"thread": "261"}), the
    isinstance(int) check silently rejected all 16 correct links, and the digest shipped
    with 0 continued threads. JSON number-vs-string is model formatting drift, not a
    different answer -- a digit string must resolve to the same thread."""
    active = [
        threads.ActiveThread(thread_id=261, label="Ukraine defence minister removal"),
        threads.ActiveThread(thread_id=12, label="Russian missile strikes on Kyiv"),
    ]
    monkeypatch.setattr(
        threads,
        "_parse_links",
        lambda _t: [
            {"story": 0, "thread": "261"},
            {"story": 1, "thread": "NEW"},
            {"story": "2", "thread": "12"},
        ],
    )
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "{}", raising=False)
    out = threads.link_threads(active, ["Zelensky fires Fedorov", "Romania drone", "Kyiv strike"])
    assert out == [261, None, 12]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (261, 261),
        ("261", 261),
        ("  261 ", 261),
        ("٣", 3),  # non-ASCII decimal digits: int() handles these
        ("２６１", 261),  # fullwidth
        ("NEW", None),
        (None, None),
        (True, None),  # bool is an int subclass; True must not alias index 1
        ("--5", None),  # str.isdigit() + lstrip("-") would reach int() and RAISE
        ("²", None),  # "².isdigit()" is True but int("²") RAISES
        ("-5", None),  # ids and indices are never negative
        ("3.0", None),
        ("", None),
    ],
)
def test_as_index_never_raises_on_model_drift(value, expected):
    """`_as_index` exists to absorb formatting drift, so it must not itself raise. The
    raise site sits outside link_threads' try/except, so a ValueError here escapes to
    run.py's blanket handler and drops the ENTIRE thread stage -- the same blast radius
    as the run-244 bug this helper was added to fix."""
    assert threads._as_index(value) == expected


def test_link_threads_errors_when_every_proposed_link_is_rejected(monkeypatch, caplog):
    """A run that PROPOSED thread ids and validated none is total continuity loss -> ERROR."""
    active = [threads.ActiveThread(thread_id=7, label="Iran nuclear deal")]
    monkeypatch.setattr(threads, "_parse_links", lambda _t: [{"story": 0, "thread": 999}])
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "{}", raising=False)
    with caplog.at_level(logging.WARNING):
        out = threads.link_threads(active, ["Iran update"])
    assert out == [None]
    assert "0 of 1" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_link_threads_warns_on_partial_rejection(monkeypatch, caplog):
    """Drift is usually partial. 1 of 2 linked still means a real continuation was demoted to
    a new thread, so it cannot be silent just because `linked > 0`."""
    active = [
        threads.ActiveThread(thread_id=7, label="Iran nuclear deal"),
        threads.ActiveThread(thread_id=8, label="Kyiv strikes"),
    ]
    monkeypatch.setattr(
        threads,
        "_parse_links",
        lambda _t: [{"story": 0, "thread": 7}, {"story": 1, "thread": 999}],
    )
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "{}", raising=False)
    with caplog.at_level(logging.WARNING):
        out = threads.link_threads(active, ["Iran update", "Kyiv update"])
    assert out == [7, None]
    assert "1 of 2" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_link_threads_errors_when_nothing_parses(monkeypatch, caplog):
    """An unparseable response loses continuity exactly like the exception path, but without a
    traceback -- the quietest possible total failure."""
    active = [threads.ActiveThread(thread_id=7, label="Iran nuclear deal")]
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "I'm sorry, I can't help.", raising=False)
    with caplog.at_level(logging.WARNING):
        out = threads.link_threads(active, ["Iran update", "Kyiv update"])
    assert out == [None, None]
    assert "no parseable links" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_link_threads_silent_on_a_genuinely_all_new_day(monkeypatch, caplog):
    """The reworded prompt asks for `null` on a new story, so an all-new day returns a FULL
    links array of nulls. Warning there would cry wolf on normal output and dilute the one
    detector that catches the real bug."""
    active = [threads.ActiveThread(thread_id=7, label="Iran nuclear deal")]
    monkeypatch.setattr(
        threads,
        "_parse_links",
        lambda _t: [{"story": 0, "thread": None}, {"story": 1, "thread": "NEW"}],
    )
    monkeypatch.setattr("claude_cli.run_sync", lambda *a, **k: "{}", raising=False)
    with caplog.at_level(logging.WARNING):
        out = threads.link_threads(active, ["Sudan floods", "Chile election"])
    assert out == [None, None]
    assert caplog.text == ""


def test_link_system_prompt_example_is_type_consistent():
    """The prompt taught the drift: its example mixed `"thread": 3` (int) with
    `"thread": "NEW"` (str) for the same field, so the model normalised to strings and
    every id came back quoted. Every `thread` value in the example must be unquoted."""
    example = threads.LINK_SYSTEM[threads.LINK_SYSTEM.index('{"links"') :]
    quoted = re.findall(r'"thread":\s*"', example)
    assert not quoted, f"example mixes a string `thread` value in: {example[:120]}"


# --- merge_thread (repairing a linker mis-split) ---------------------------
#
# When the linker fails to recognise a continuation it creates a DUPLICATE thread for a
# story already being tracked (run 244, 2026-07-25: 5 of them). Retiring the duplicate
# leaves the real thread with a hole in its arc and an under-counted "day N" badge, since
# render_context counts installment rows. Merging folds the duplicate's history back in.


def _thread_state(conn, tid):
    row = conn.execute("SELECT label, status, first_run_id, last_run_id FROM threads WHERE id = ?", (tid,)).fetchone()
    installments = conn.execute(
        "SELECT run_id, cluster_story, content FROM thread_installments WHERE thread_id = ? ORDER BY run_id", (tid,)
    ).fetchall()
    questions = conn.execute("SELECT question FROM thread_questions WHERE thread_id = ? ORDER BY id", (tid,)).fetchall()
    return row, installments, questions


def test_merge_thread_folds_duplicate_history_into_the_real_thread(conn):
    """The duplicate holds the NEWEST label (what the linker matches on next run) while the
    target holds the arc. After the merge the target must own both."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("Cap Ferret wildfire evacuation France", run_id=1)
    store.record_installment(target, 1, "Cap Ferret wildfire evacuation France", is_new=True)
    store.set_installment_content(target, 1, '{"whats_new": ["fire reaches Cap Ferret"]}')
    store.add_questions(target, ["Will Bordeaux be evacuated?"], run_id=1)

    dup = store.create_thread("Spain and France wildfires mass evacuation", run_id=2)
    store.record_installment(dup, 2, "Spain and France wildfires mass evacuation", is_new=True)
    store.add_questions(dup, ["How many hectares burned?"], run_id=2)

    result = store.merge_thread(dup, target)

    assert result == {"installments_moved": 1, "installments_dropped": 0, "questions_moved": 1}
    row, installments, questions = _thread_state(conn, target)
    # Label advances to the duplicate's (newer) one; first_run_id keeps the EARLIER origin.
    assert row == ("Spain and France wildfires mass evacuation", "active", 1, 2)
    assert [i[0] for i in installments] == [1, 2]  # arc has no hole -> day count = 2
    assert installments[0][2] == '{"whats_new": ["fire reaches Cap Ferret"]}'  # content preserved
    assert len(questions) == 2  # open ledger from both sides
    assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (dup,)).fetchone()[0] == 0


def test_merge_thread_day_count_matches_installments(conn):
    """render_context derives the badge from installment COUNT, so the merge is what makes
    'day N' truthful again."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("thread a", run_id=1)
    store.record_installment(target, 1, "thread a", is_new=True)
    store.record_installment(target, 2, "thread a cont", is_new=False)
    dup = store.create_thread("thread a renamed", run_id=3)
    store.record_installment(dup, 3, "thread a renamed", is_new=True)

    assert store.render_context(target, 3)["day"] == 2
    store.merge_thread(dup, target)
    assert store.render_context(target, 3)["day"] == 3


def test_merge_thread_drops_colliding_run_keeping_synthesized_content(conn):
    """Both threads carrying an installment for the SAME run would double-count the day.
    The row with synthesized content wins; the bare identity row is dropped."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("target", run_id=1)
    store.record_installment(target, 1, "target", is_new=True)  # no content
    dup = store.create_thread("dup", run_id=1)
    store.record_installment(dup, 1, "dup", is_new=True)
    store.set_installment_content(dup, 1, '{"whats_new": ["the real delta"]}')

    result = store.merge_thread(dup, target)

    assert result["installments_dropped"] == 1
    _, installments, _ = _thread_state(conn, target)
    assert len(installments) == 1  # exactly one row for run 1 -> day count stays 1
    assert installments[0][2] == '{"whats_new": ["the real delta"]}'  # content survived


def test_merge_thread_keeps_target_label_when_duplicate_is_older(conn):
    """Only advance the label/last_run_id if the duplicate is genuinely newer."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("newer label", run_id=3)
    store.record_installment(target, 3, "newer label", is_new=True)
    dup = store.create_thread("older label", run_id=1)
    store.record_installment(dup, 1, "older label", is_new=True)

    store.merge_thread(dup, target)

    row, installments, _ = _thread_state(conn, target)
    assert row == ("newer label", "active", 1, 3)  # label unchanged, origin backdated
    assert [i[0] for i in installments] == [1, 3]


def test_nested_transaction_is_atomic_across_several_merges(conn):
    """A batch repair is the whole point of this method, so wrapping merges in one
    `transaction()` must be all-or-nothing. A non-re-entrant manager would let the inner
    merge commit immediately AND clear the defer flag, so everything after it self-commits
    and the rollback silently protects nothing."""
    store = threads.ThreadStore(conn)
    t1, t2 = store.create_thread("t1", run_id=1), store.create_thread("t2", run_id=1)
    store.record_installment(t1, 1, "t1", is_new=True)
    store.record_installment(t2, 1, "t2", is_new=True)
    d1, d2 = store.create_thread("d1", run_id=2), store.create_thread("d2", run_id=2)
    store.record_installment(d1, 2, "d1", is_new=True)
    store.record_installment(d2, 2, "d2", is_new=True)

    with pytest.raises(RuntimeError), store.transaction():
        store.merge_thread(d1, t1)
        store.merge_thread(d2, t2)
        raise RuntimeError("batch aborted")

    fresh = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2])
    survivors = {r[0] for r in fresh.execute("SELECT id FROM threads")}
    assert {d1, d2} <= survivors, "aborted batch left merges committed"
    assert fresh.execute("SELECT COUNT(*) FROM thread_installments WHERE thread_id = ?", (t1,)).fetchone()[0] == 1
    fresh.close()


def test_merge_thread_does_not_resurrect_a_resolved_question(conn):
    """Duplicate threads for one story generate the SAME question text. Moving the
    duplicate's open copy onto a target that already resolved it puts an answered question
    back into `OPEN QUESTIONS:` in the synthesis prompt -- telling the model settled
    material is still unanswered, which is how a thread re-reports old news as new."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("target", run_id=1)
    store.record_installment(target, 1, "target", is_new=True)
    store.add_questions(target, ["Will Bordeaux be evacuated?"], run_id=1)
    store.resolve_question(target, "Will Bordeaux be evacuated?", run_id=2, how="prefecture confirmed")

    dup = store.create_thread("dup", run_id=2)
    store.record_installment(dup, 2, "dup", is_new=True)
    store.add_questions(dup, ["Will Bordeaux be evacuated?", "How many hectares?"], run_id=2)

    store.merge_thread(dup, target)

    assert store.open_questions(target) == ["How many hectares?"]


def test_merge_thread_collapses_preexisting_duplicate_installments(conn):
    """There is no UNIQUE(thread_id, run_id) constraint, so the merge must not assume one
    row per side -- otherwise a stray pair inflates the reader-visible day count and the
    reported drop count is wrong in exactly the case you would check it."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("target", run_id=1)
    store.record_installment(target, 1, "target", is_new=True)
    dup = store.create_thread("dup", run_id=1)
    store.record_installment(dup, 1, "dup a", is_new=True)
    store.record_installment(dup, 1, "dup b", is_new=True)  # stray duplicate
    store.set_installment_content(dup, 1, '{"whats_new": ["kept"]}')

    result = store.merge_thread(dup, target)

    rows = conn.execute(
        "SELECT content FROM thread_installments WHERE thread_id = ? AND run_id = 1", (target,)
    ).fetchall()
    assert len(rows) == 1, "day count would be inflated"
    assert rows[0][0] == '{"whats_new": ["kept"]}'  # the synthesized row won
    assert result["installments_dropped"] == 2  # both losers, counted accurately


def test_merge_thread_is_idempotent_and_refuses_self_merge(conn):
    """A repair script may be re-run; a second pass must be a harmless no-op."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("t", run_id=1)
    store.record_installment(target, 1, "t", is_new=True)
    dup = store.create_thread("d", run_id=2)
    store.record_installment(dup, 2, "d", is_new=True)

    store.merge_thread(dup, target)
    assert store.merge_thread(dup, target) is None  # source already gone

    with pytest.raises(ValueError):
        store.merge_thread(target, target)
    with pytest.raises(ValueError):
        store.merge_thread(target, 99999)  # unknown target must not orphan rows


class _FailOnDeleteThreads:
    """Connection proxy that raises on the final `DELETE FROM threads`. sqlite3.Connection
    is a C type whose `execute` cannot be monkeypatched, so wrap it instead."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *a):
        if sql.strip().upper().startswith("DELETE FROM THREADS"):
            raise sqlite3.OperationalError("boom")
        return self._conn.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_merge_thread_rolls_back_completely_on_failure(conn):
    """Half a merge (history moved, duplicate still present) is worse than none."""
    store = threads.ThreadStore(conn)
    target = store.create_thread("t", run_id=1)
    store.record_installment(target, 1, "t", is_new=True)
    dup = store.create_thread("d", run_id=2)
    store.record_installment(dup, 2, "d", is_new=True)

    store.conn = _FailOnDeleteThreads(conn)
    with pytest.raises(sqlite3.OperationalError):
        store.merge_thread(dup, target)
    store.conn = conn

    assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (dup,)).fetchone()[0] == 1
    _, installments, _ = _thread_state(conn, target)
    assert [i[0] for i in installments] == [1]  # nothing moved
    dup_rows = conn.execute("SELECT run_id FROM thread_installments WHERE thread_id = ?", (dup,)).fetchall()
    assert [r[0] for r in dup_rows] == [2]  # duplicate's own history intact


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


def test_resolve_threads_carries_recent_deltas_and_questions(conn):
    import json

    store = threads.ThreadStore(conn)
    a1 = threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    tid = a1[0].thread_id
    # B writes the run-1 installment (its whats_new facts become the memory) + a question.
    store.set_installment_content(tid, 1, json.dumps({"whats_new": [{"fact": "A ceasefire was signed near Hormuz."}]}))
    store.add_questions(tid, ["Will the ceasefire hold?"], run_id=1)

    a2 = threads.resolve_threads(
        [{"story": "Iran ceasefire holds as shipping resumes"}], run_id=2, store=store, linker=anchor_linker
    )
    assert any("ceasefire" in u for u in a2[0].recent_updates)  # prior delta carried as memory
    assert "Will the ceasefire hold?" in a2[0].open_questions


def test_active_threads_returns_label_for_linker(conn):
    store = threads.ThreadStore(conn)
    store.create_thread("Iran nuclear talks", run_id=1)
    active = store.active_threads(before_run_id=2, dormant_after=3)
    assert active[0].label == "Iran nuclear talks"
    # no installments yet -> recent_labels falls back to [label] (the linker still sees something)
    assert active[0].recent_labels == ["Iran nuclear talks"]


def test_active_threads_carries_recent_label_history(conn):
    # The linker must see the story ARC, not just the latest (possibly drifted) label -- otherwise
    # a thread whose label has moved on ("Starmer resigns") fails to match its own continuation
    # ("Burnham to become PM") and the storyline gets re-threaded with a fresh id.
    store = threads.ThreadStore(conn)
    tid = store.create_thread("Makerfield by-election", run_id=1)
    store.record_installment(tid, 1, "Makerfield by-election", is_new=True)
    store.touch_thread(tid, "Burnham wins, leadership contest", run_id=2)
    store.record_installment(tid, 2, "Burnham wins, leadership contest", is_new=False)
    store.touch_thread(tid, "Starmer resigns as UK PM", run_id=3)
    store.record_installment(tid, 3, "Starmer resigns as UK PM", is_new=False)

    active = store.active_threads(before_run_id=4, dormant_after=3)
    assert active[0].label == "Starmer resigns as UK PM"  # latest still available
    assert active[0].recent_labels == [
        "Makerfield by-election",
        "Burnham wins, leadership contest",
        "Starmer resigns as UK PM",
    ]  # oldest -> newest, the arc the linker needs


def test_active_threads_recent_labels_capped_and_ordered(conn):
    store = threads.ThreadStore(conn)
    for rid in range(4, 7):  # fixture seeds runs 1-3; this arc spans six
        conn.execute("INSERT INTO digest_runs (id, git_sha) VALUES (?, ?)", (rid, f"r{rid}"))
    conn.commit()
    tid = store.create_thread("day1", run_id=1)
    for r in range(1, 7):
        if r > 1:
            store.touch_thread(tid, f"day{r}", run_id=r)
        store.record_installment(tid, r, f"day{r}", is_new=(r == 1))
    active = store.active_threads(before_run_id=7, dormant_after=10)
    # only the most recent RECENT_LABELS_K, oldest->newest
    assert active[0].recent_labels == [f"day{r}" for r in range(7 - threads.RECENT_LABELS_K, 7)]


def test_render_context_returns_day_and_delta(conn):
    import json

    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran", run_id=1)
    store.record_installment(tid, 1, "Iran", is_new=True)
    store.record_installment(tid, 2, "Iran", is_new=False)
    store.set_installment_content(
        tid, 2, json.dumps({"whats_new": [{"fact": "The US struck 10 targets."}, {"fact": "Iran retaliated."}]})
    )
    assert store.render_context(tid, 2) == {"day": 2, "delta": "The US struck 10 targets. Iran retaliated."}


def test_delta_from_facts_joins_top_n_in_order():
    facts = [{"fact": "Lead development."}, {"fact": "Second."}, {"fact": "Third."}, {"fact": "Fourth."}]
    assert threads.delta_from_facts(facts, top_n=2) == "Lead development. Second."
    assert threads.delta_from_facts([]) == ""


def test_resolve_threads_ages_out_dormant_threads(conn):
    store = threads.ThreadStore(conn)
    threads.resolve_threads(
        [{"story": "Iran ceasefire over Strait of Hormuz"}], run_id=1, store=store, linker=anchor_linker
    )
    # Five COMPLETED runs pass with the story absent (> dormant_after=3); run 9 is the
    # one doing the linking, and must exist for the FK.
    for rid in range(4, 10):
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
    for rid in range(4, 10):  # five FAILED runs (no completed_at) + run 9, the linking run
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


# --- strip_article_ids: leak guard for inline [A123] citations in synthesized prose ---
# The synthesis model sometimes embeds source-id citations inline in fact text (they belong
# only in the separate `sources` provenance field). They must never reach reader-facing text
# or the thread's carried memory. This is the thread path's analog of COHERENCE's leak guard.


def test_strip_article_ids_removes_trailing_multi_id_citation():
    assert threads.strip_article_ids("Talks resumed in Doha. [A221, A407]") == "Talks resumed in Doha."


def test_strip_article_ids_removes_midsentence_citation_without_double_space():
    assert threads.strip_article_ids("Iran denied it [A164, A407, A429] and stalled.") == "Iran denied it and stalled."


def test_strip_article_ids_removes_single_id():
    assert threads.strip_article_ids("Quake hit northern Venezuela [A119]") == "Quake hit northern Venezuela"


def test_strip_article_ids_handles_no_space_list():
    assert threads.strip_article_ids("x [A1,A2,A3] y") == "x y"


def test_strip_article_ids_removes_leading_citation():
    assert threads.strip_article_ids("[A1] Big news today") == "Big news today"


def test_strip_article_ids_collapses_multiple_separated_citations():
    assert threads.strip_article_ids("a [A1] b [A2] c") == "a b c"


def test_strip_article_ids_preserves_numeric_source_markers():
    # [1]/[2] are legit rendered source markers, not article ids -- must survive.
    assert threads.strip_article_ids("See report [1] and [2]") == "See report [1] and [2]"


def test_strip_article_ids_preserves_non_id_brackets():
    assert threads.strip_article_ids("a note [draft] here") == "a note [draft] here"


def test_strip_article_ids_passthrough_clean_text():
    assert threads.strip_article_ids("Nothing to strip here.") == "Nothing to strip here."


def test_strip_article_ids_empty_string():
    assert threads.strip_article_ids("") == ""


def test_delta_from_facts_strips_inline_article_ids():
    # delta_from_facts is the single funnel for both the rendered delta and the carried memory
    # (recent_deltas), so stripping here keeps reader-facing text AND model memory id-free even
    # if a stored fact still carries an inline citation.
    facts = [{"fact": "Talks resumed in Doha. [A221, A407]"}, {"fact": "Iran stalled [A164]"}]
    assert threads.delta_from_facts(facts) == "Talks resumed in Doha. Iran stalled"


def test_render_context_strips_ids_from_already_stored_dirty_facts(conn):
    # Resilience: a fact persisted BEFORE the leak fix (inline citation still in the text) must
    # still render id-free, since the strip happens on read in delta_from_facts. This is what
    # lets the stored-content remediation be cosmetic rather than load-bearing.
    import json

    store = threads.ThreadStore(conn)
    tid = store.create_thread("Iran", run_id=1)
    store.record_installment(tid, 1, "Iran", is_new=True)
    store.record_installment(tid, 2, "Iran", is_new=False)
    store.set_installment_content(
        tid,
        2,
        json.dumps({"whats_new": [{"fact": "Doha talks set this week. [A221, A407]", "sources": ["A221", "A407"]}]}),
    )
    ctx = store.render_context(tid, 2)
    assert "[A" not in ctx["delta"]
    assert ctx["delta"] == "Doha talks set this week."


# --- strip_article_ids: the parenthesised form (run 247, 2026-07-28) ---
# The cases above cover the bracketed form the 2026-06-30 fix was built for. Run 247 proved the
# models also write "(A316)". Worth covering on the thread path specifically: it feeds both the
# rendered delta and the carried recent_deltas memory, so a leak here is shown AND persisted.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Talks resumed in Doha. (A221)", "Talks resumed in Doha."),
        ("Iran denied it (A164, A407) and stalled.", "Iran denied it and stalled."),
        ("Strikes resumed (A316, A317 and A318).", "Strikes resumed."),
        ("The vote split (A110; A263)", "The vote split"),
        ("Aid convoys turned back (A11 & A12)", "Aid convoys turned back"),
    ],
    ids=["trailing", "midsentence-multi", "and-join", "semicolon-join", "ampersand-join"],
)
def test_strip_article_ids_removes_parenthesised_citations(text, expected):
    assert threads.strip_article_ids(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The council met (see annex) before the vote.",
        "Casualties (at least 40) remain unconfirmed.",
        # Mismatched delimiters are not a form any generator produces, so matching them would only
        # add false positives -- and a false positive silently edits reader text.
        "Split ruling [A316) stands",
    ],
    ids=["parenthetical-phrase", "parenthetical-number", "mismatched-delimiters"],
)
def test_strip_article_ids_leaves_legitimate_delimited_text(text):
    assert threads.strip_article_ids(text) == text


# --- bare self-citations: the 2026-07-12 leak ---
# strip_article_ids only removes DELIMITED groups, because a bare "A19" cannot be told from
# "the A19 chip" by any regex. But the synthesis writes bare trailing citations -- 2026-07-12
# shipped "...according to A238." to subscribers and the public archive, and it is still there.
#
# The thread path has ground truth a regex does not: each fact declares its own `sources`. An
# id that appears in BOTH the fact text and that fact's sources is a self-citation, not an
# aircraft. Facts carrying one are skipped and the next-ranked fact is promoted, so the delta
# stays full-length instead of shipping the leak or a mangled "according to ." stub.


def test_delta_skips_fact_with_bare_self_citation_and_promotes_the_next():
    facts = [
        {"fact": "600 of 1,500 evacuees returned as the fire nears control, according to A238.", "sources": ["A238"]},
        {"fact": "Spanish authorities suspect arson.", "sources": ["A254"]},
        {"fact": "Third fact.", "sources": ["A243"]},
        {"fact": "Fourth fact.", "sources": ["A9"]},
    ]
    # Leaky lead dropped; the 4th is promoted so the reader still gets three facts.
    assert threads.delta_from_facts(facts) == "Spanish authorities suspect arson. Third fact. Fourth fact."


def test_delta_keeps_a_designator_that_is_not_a_self_citation():
    # "A19" here is an Apple chip, not a citation -- it is absent from sources, so it stays.
    facts = [{"fact": "The iPhone 17e ships with the A19 chip.", "sources": ["A404"]}]
    assert threads.delta_from_facts(facts) == "The iPhone 17e ships with the A19 chip."


def test_delta_still_strips_delimited_citations_without_dropping_the_fact():
    # Delimited groups remain strippable in place -- removing "(A238)" leaves readable prose,
    # so there is no reason to lose the fact.
    facts = [{"fact": "Talks resumed in Doha. (A238)", "sources": ["A238"]}]
    assert threads.delta_from_facts(facts) == "Talks resumed in Doha."


def test_delta_drops_fact_whose_bare_citation_survives_when_no_replacement_exists():
    facts = [{"fact": "Fire nears control, according to A238.", "sources": ["A238"]}]
    assert threads.delta_from_facts(facts) == ""


# --- malformed shapes: model JSON drift must not eat clean facts or kill the run ---
# The self-citation check reads model-authored JSON, so every container and element type is
# untrusted. It must also agree with the Rust mirror, which tolerates all of these by virtue
# of serde's typed accessors -- a divergence means the email and the archive page disagree
# about the same stored row.


@pytest.mark.parametrize(
    "sources",
    ["A3", {"A": 1}, 42, None],
    ids=["scalar-string", "dict", "int", "null"],
)
def test_delta_keeps_clean_fact_when_sources_is_not_a_list(sources):
    # A scalar "A3" iterates as ['A', '3'], so \bA\b and \b3\b were matched against the prose
    # and dropped clean facts containing a lone capital or a bare digit.
    facts = [{"fact": "Talks resumed at 3 p.m. Plan A was rejected.", "sources": sources}]
    assert threads.delta_from_facts(facts) == "Talks resumed at 3 p.m. Plan A was rejected."


def test_delta_survives_non_string_fact_beyond_the_top_n():
    # Skip-and-promote walks past index 3, so entries the old facts[:3] never touched are now
    # evaluated. A raise here loses ALL thread enrichment for the run, not just one fact.
    facts = [
        {"fact": "Leak, per A1.", "sources": ["A1"]},
        {"fact": "One."},
        {"fact": "Two."},
        {"fact": 123},
    ]
    assert threads.delta_from_facts(facts) == "One. Two."


def test_delta_survives_non_dict_fact_entry():
    assert threads.delta_from_facts([{"fact": "One."}, "not an object", {"fact": "Two."}]) == "One. Two."


def test_delta_detects_self_citation_despite_padded_source_id():
    # Rust's hand-rolled boundary check keeps a padded id (it is "the only guard" on the public
    # archive page), so Python must not diverge -- trim both sides before matching.
    facts = [{"fact": "Fire nears control, according to A238 today.", "sources": ["A238 "]}]
    assert threads.delta_from_facts(facts) == ""


# --- selected_labels must key on article_ids, not SELECT's positional index ---
# `cluster_index` is a 0-based position into a 319-element array, produced by a model counting
# entries by eye. Run 247: 7 of 12 should_know entries pointed at a cluster containing NONE of
# their own articles, always undercounting, offsets growing with position (+1,+23,+23,+23,+43,
# +43,+47). The article_ids in those same entries were unanimous every time. So the thread
# label was derived from the unreliable half of the entry, and 7 stories were filed under
# labels describing other stories entirely -- unable to ever match merge's cluster_id.


def _clusters_doc(*groups):
    return {"clusters": [{"story": s, "article_ids": ids} for s, ids in groups]}


def test_selected_labels_uses_article_ids_not_a_wrong_cluster_index():
    clusters = _clusters_doc(("wrong story", ["A1"]), ("filler", ["A2"]), ("real story", ["A5", "A6", "A7"]))
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A5", "A6", "A7"]}], "should_know": []}

    out = threads.selected_labels(clusters, selected)

    assert [e["story"] for e in out] == ["real story"]


def test_selected_labels_uses_plurality_when_ids_span_clusters():
    clusters = _clusters_doc(("minority", ["A1", "A2"]), ("majority", ["A5", "A6", "A7"]))
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A5", "A6", "A7"]}], "should_know": []}

    out = threads.selected_labels(clusters, selected)

    assert out[0]["story"] == "majority"


def test_selected_labels_skips_an_entry_whose_ids_map_nowhere():
    # Labelling from the index while synthesizing from unmappable ids is the exact
    # label/articles split this change exists to remove: a thread publicly labelled from
    # cluster X, built from articles in no cluster, and able to seize the thread the correct
    # story would have continued. A missing "Ongoing" badge is the cheaper failure.
    clusters = _clusters_doc(("indexed story", ["A1"]))
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A999"]}], "should_know": []}

    assert threads.selected_labels(clusters, selected) == []


def test_selected_labels_still_uses_the_index_when_the_entry_has_no_ids_at_all():
    # The only case the index is the sole signal available. Never fired in 487 archived
    # entries, but it is the pre-existing contract and costs nothing to keep.
    clusters = _clusters_doc(("indexed story", ["A1", "A2"]))
    selected = {"must_know": [{"cluster_index": 0}], "should_know": []}

    out = threads.selected_labels(clusters, selected)

    assert out[0]["story"] == "indexed story"
    assert out[0]["article_ids"] == ["A1", "A2"]


def test_selected_labels_ignores_non_string_ids_rather_than_dying():
    # article_ids is raw model JSON. An unhashable element would raise into run.py's blanket
    # handler and take the whole thread stage down for the run.
    clusters = _clusters_doc(("real story", ["A5"]))
    selected = {"must_know": [{"cluster_index": 0, "article_ids": [["A9"], "A5"]}], "should_know": []}

    assert threads.selected_labels(clusters, selected)[0]["story"] == "real story"


def test_selected_labels_counts_each_article_once():
    clusters = _clusters_doc(("own", ["A1", "A2", "A3"]), ("other", ["A9"]))
    selected = {
        "must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2", "A3", "A9", "A9", "A9", "A9"]}],
        "should_know": [],
    }

    assert threads.selected_labels(clusters, selected)[0]["story"] == "own"


def test_selected_labels_keeps_the_entrys_own_article_ids():
    clusters = _clusters_doc(("real story", ["A5", "A6", "A7"]))
    selected = {"must_know": [{"cluster_index": 99, "article_ids": ["A5", "A6"]}], "should_know": []}

    out = threads.selected_labels(clusters, selected)

    assert out[0]["article_ids"] == ["A5", "A6"]
