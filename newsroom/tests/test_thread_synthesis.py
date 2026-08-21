"""Tests for thread_synthesis.py -- threaded synthesis + ledger (sub-project B).

The LLM calls (synthesize_installment, audit_whats_new) are injected as fakes so the
persistence logic (audit-drop, narrative update, question resolution) and orchestration run
with no model in CI. Parsing + bundling are tested directly.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db
import thread_synthesis as ts
import threads

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"

ARTS = {
    "A1": {"title": "Iran and US sign ceasefire", "summary": "A ceasefire was signed in Geneva."},
    "A2": {"title": "Shipping resumes in Hormuz", "summary": "Tankers transit the strait again."},
}


@pytest.fixture
def store(tmp_path):
    db._state = db._State()
    db_path = tmp_path / "test.db"
    db.init(db_path, MIGRATIONS_DIR)
    c = sqlite3.connect(db_path)
    for sha in ("r1", "r2"):
        c.execute("INSERT INTO digest_runs (git_sha) VALUES (?)", (sha,))
    c.commit()
    return threads.ThreadStore(c)


# --- pure helpers ----------------------------------------------------------


def test_parse_json_ignores_trailing_prose():
    assert ts._parse_json('{"whats_new": []}\nthat is all') == {"whats_new": []}


def test_bundle_formats_cited_articles_only():
    out = ts._bundle(["A1", "A9"], ARTS)
    assert "A1: Iran and US sign ceasefire" in out
    assert "A9" not in out  # missing article skipped, no crash


# --- late-binding neighbourhood (sub-project D) ----------------------------


def test_expand_neighbourhood_pulls_entity_neighbours_not_unrelated():
    arts = {
        "A1": {"title": "Iran and US sign nuclear ceasefire in Geneva", "summary": "Iran deal."},
        "A2": {"title": "Iran tankers transit Strait of Hormuz", "summary": "Iran shipping resumes."},
        "A3": {"title": "Brazil holds carnival parade in Rio", "summary": "Unrelated festival."},
    }
    out = ts.expand_neighbourhood(["A1"], arts, threshold=0.1, max_extra=5)
    assert "A1" in out
    assert "A2" in out  # shares Iran entities
    assert "A3" not in out  # unrelated -> not pulled


def test_expand_neighbourhood_respects_max_extra_and_threshold():
    arts = {f"A{i}": {"title": "Iran nuclear deal talks", "summary": "Iran"} for i in range(6)}
    out = ts.expand_neighbourhood(["A0"], arts, threshold=0.5, max_extra=2)
    assert out[0] == "A0" and len(out) == 3  # seed + at most 2 extras


def test_expand_neighbourhood_no_signal_returns_seed():
    arts = {"A1": {"title": "", "summary": ""}, "A2": {"title": "x", "summary": ""}}
    assert ts.expand_neighbourhood(["A1"], arts, threshold=0.3, max_extra=5) == ["A1"]


def test_expand_neighbourhood_idf_strips_hub_entities_to_prevent_overpull():
    # >=30 articles so IDF kicks in. "Trump" is a hub (in every article) and must NOT fuse the
    # 30 unrelated Trump-filler stories to the Iran seed; only the genuine Iran neighbours pull.
    arts = {"A0": {"title": "Iran nuclear deal with Trump", "summary": "Iran"}}
    for i in range(1, 31):
        arts[f"F{i}"] = {"title": "Trump speech in Washington", "summary": "Trump politics"}
    arts["R1"] = {"title": "Iran Hormuz shipping under Trump", "summary": "Iran"}
    arts["R2"] = {"title": "Iran oil exports and Trump", "summary": "Iran"}
    out = ts.expand_neighbourhood(["A0"], arts, threshold=0.2, max_extra=20)
    assert set(out) == {"A0", "R1", "R2"}  # Iran neighbours pulled; 30 Trump-hub fillers excluded


# --- apply_installment -----------------------------------------------------


def _seed_thread(store, *, open_qs=()):
    tid = store.create_thread("Iran ceasefire", run_id=1)
    store.record_installment(tid, 2, "Iran ceasefire holds", is_new=False)  # B updates this run-2 row
    if open_qs:
        store.add_questions(tid, list(open_qs), run_id=1)
    return tid


def test_apply_installment_drops_unsupported_facts(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="Iran ceasefire holds")
    installment = {
        "whats_new": [
            {"fact": "Ceasefire signed", "sources": ["A1"]},
            {"fact": "Fabricated casualty figure", "sources": ["A1"]},
        ],
        "updated_narrative": "Iran and the US signed a ceasefire.",
    }
    verified = ts.apply_installment(store, a, installment, supported=[True, False], run_id=2)
    assert [f["fact"] for f in verified["whats_new"]] == ["Ceasefire signed"]
    # persisted installment content matches the verified (dropped) set
    row = store.conn.execute(
        "SELECT content FROM thread_installments WHERE thread_id=? AND run_id=2", (tid,)
    ).fetchone()
    assert len(json.loads(row[0])["whats_new"]) == 1


def test_apply_installment_stores_verified_content(store):
    import json

    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="x")
    ts.apply_installment(store, a, {"whats_new": [{"fact": "f", "sources": ["A1"]}]}, supported=[True], run_id=2)
    content = store.conn.execute(
        "SELECT content FROM thread_installments WHERE thread_id=? AND run_id=2", (tid,)
    ).fetchone()[0]
    assert json.loads(content)["whats_new"] == [{"fact": "f", "sources": ["A1"]}]


def test_apply_installment_resolves_only_carried_questions(store):
    tid = _seed_thread(store, open_qs=["Will the ceasefire hold?"])
    a = threads.ThreadAssignment(
        thread_id=tid, is_new=False, cluster_story="x", open_questions=["Will the ceasefire hold?"]
    )
    installment = {
        "resolved": [
            {"question": "Will the ceasefire hold?", "how": "Both sides reaffirmed it."},
            {"question": "A question never asked", "how": "ignored"},  # not carried -> not resolved
        ],
        "new_questions": ["Who monitors compliance?"],
    }
    ts.apply_installment(store, a, installment, supported=[], run_id=2)
    rows = dict(
        store.conn.execute("SELECT question, status FROM thread_questions WHERE thread_id=?", (tid,)).fetchall()
    )
    assert rows["Will the ceasefire hold?"] == "resolved"
    assert rows["Who monitors compliance?"] == "open"
    assert "A question never asked" not in rows  # phantom resolve created nothing


# --- synthesize_threads orchestration --------------------------------------


def test_synthesize_threads_skips_new_and_thin_threads(store):
    new = threads.ThreadAssignment(thread_id=1, is_new=True, cluster_story="new", article_ids=["A1", "A2"])
    thin = threads.ThreadAssignment(thread_id=2, is_new=False, cluster_story="thin", article_ids=["A1"])
    called = []
    ts.synthesize_threads(
        [new, thin],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *a, **k: called.append(1) or {"whats_new": []},
        audit_fn=lambda *a, **k: [],
    )
    assert called == []  # neither qualifies (new / too few articles)


def test_synthesize_threads_synthesizes_continuing_thread(store):
    tid = _seed_thread(store, open_qs=["Will the ceasefire hold?"])
    a = threads.ThreadAssignment(
        thread_id=tid,
        is_new=False,
        cluster_story="Iran ceasefire holds",
        article_ids=["A1", "A2"],
        open_questions=["Will the ceasefire hold?"],
    )
    fake_installment = {
        "whats_new": [{"fact": "Ceasefire signed", "sources": ["A1"]}, {"fact": "bad", "sources": ["A1"]}],
        "resolved": [{"question": "Will the ceasefire hold?", "how": "reaffirmed"}],
        "new_questions": ["Who monitors it?"],
        "updated_narrative": "A ceasefire holds.",
    }
    out, _ = ts.synthesize_threads(
        [a],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *args, **k: fake_installment,
        audit_fn=lambda *args, **k: [True, False],  # drop the second fact
    )
    assert len(out) == 1
    assert [f["fact"] for f in out[0]["whats_new"]] == ["Ceasefire signed"]
    assert out[0]["thread_id"] == tid
    # ledger updated
    rows = dict(
        store.conn.execute("SELECT question, status FROM thread_questions WHERE thread_id=?", (tid,)).fetchall()
    )
    assert rows["Will the ceasefire hold?"] == "resolved"


def test_synthesize_threads_skips_failing_thread_without_crashing(store):
    tid = _seed_thread(store)
    good = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])

    def boom(*a, **k):
        raise RuntimeError("synthesis exploded")

    out, _ = ts.synthesize_threads([good], ARTS, run_id=2, store=store, synth_fn=boom, audit_fn=lambda *a, **k: [])
    assert out == []  # failure swallowed, no exception propagated


def test_audit_whats_new_empty_is_empty():
    assert ts.audit_whats_new([], ARTS) == []


def test_audit_whats_new_raises_on_llm_error(monkeypatch):
    # audit now RAISES on LLM/parse failure; synthesize_threads owns the fail-open + counting.
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    wn = [{"fact": "x", "sources": ["A1"]}]
    with pytest.raises(RuntimeError):
        ts.audit_whats_new(wn, ARTS)


def test_audit_whats_new_maps_each_claim_to_its_verdict(monkeypatch):
    monkeypatch.setattr(
        ts, "_run_sonnet", lambda *a, **k: '{"verdicts": [{"id": 1, "supported": true}, {"id": 2, "supported": false}]}'
    )
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    assert ts.audit_whats_new(wn, ARTS) == [True, False]


def test_audit_whats_new_reasks_once_when_the_first_reply_is_short(monkeypatch):
    # Run 271's real failure: 6 claims, one verdict back ("got ids [1]"). The reply is malformed,
    # not the endpoint -- so re-ask once before spending the fail-open. Sampling makes the second
    # draw independent, which is the whole reason a single re-ask is worth a call.
    replies = iter(
        [
            '{"verdicts": [{"id": 1, "supported": true}]}',
            '{"verdicts": [{"id": 1, "supported": true}, {"id": 2, "supported": false}]}',
        ]
    )
    prompts = []

    def fake(user, *a, **k):
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(ts, "_run_sonnet", fake)
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    assert ts.audit_whats_new(wn, ARTS) == [True, False]
    assert len(prompts) == 2
    # The re-ask has to SAY what went wrong, or it is just a second identical roll of the dice.
    assert prompts[1].startswith(prompts[0])
    assert "EXACTLY 2 verdicts" in prompts[1]
    assert "ids [1]" in prompts[1]


def test_audit_whats_new_reasks_once_when_the_first_reply_is_not_json(monkeypatch):
    # Same class of failure, different shape: prose instead of an object. _parse_json raises
    # ValueError, which must be re-asked rather than spent as a fail-open.
    replies = iter(["I could not evaluate these claims.", '{"verdicts": [{"id": 1, "supported": false}]}'])
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: next(replies))
    assert ts.audit_whats_new([{"fact": "a", "sources": ["A1"]}], ARTS) == [False]


def test_audit_whats_new_raises_after_the_reask_also_misaligns(monkeypatch):
    # The re-ask is ONE extra call, not a loop: a persistently-broken auditor must still reach the
    # caller's fail-open + count + alert, and must not sit in a retry spiral inside a daily run.
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return '{"verdicts": [{"id": 1, "supported": true}]}'

    monkeypatch.setattr(ts, "_run_sonnet", fake)
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    with pytest.raises(ValueError):
        ts.audit_whats_new(wn, ARTS)
    assert len(calls) == 2


def test_audit_whats_new_rejects_a_reply_that_has_the_shape_but_no_judgment(monkeypatch):
    """The quiet failure the re-ask could otherwise MANUFACTURE.

    _AUDIT_REASK presses for a verdict COUNT, and every constraint in it is about shape. A model
    that is not tracking the claims can satisfy that exactly -- right ids, no `supported` -- and a
    default of True would then return "all facts supported" with audit_failures at 0. That is
    strictly worse than run 271, which at least alerted. A verdict with no boolean is not a
    judgment, so it must reach the raise.
    """
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: '{"verdicts": [{"id": 1}, {"id": 2}]}')
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    with pytest.raises(ValueError):
        ts.audit_whats_new(wn, ARTS)


def test_audit_whats_new_does_not_read_a_string_verdict_as_supported(monkeypatch):
    # bool("false") is True, so coercion inverted an auditor that had done its job and shipped the
    # fact it rejected. An unreadable verdict now reads as unsupported, so the fact drops.
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: '{"verdicts": [{"id": 1, "supported": "false"}]}')
    assert ts.audit_whats_new([{"fact": "a", "sources": ["A1"]}], ARTS) == [False]


def test_audit_whats_new_rejects_duplicate_and_out_of_range_ids(monkeypatch):
    # Two verdicts for claim 1 means claim 2 was never judged, even though every id present is
    # valid -- and dict() would collapse the pair, discarding one real verdict without a word.
    monkeypatch.setattr(
        ts,
        "_run_sonnet",
        lambda *a, **k: '{"verdicts": [{"id": 1, "supported": false}, {"id": 1, "supported": true}]}',
    )
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    with pytest.raises(ValueError):
        ts.audit_whats_new(wn, ARTS)


def test_audit_whats_new_survives_a_verdicts_list_of_junk(monkeypatch):
    # `{"verdicts": {"1": true}}` and a stray bare string used to raise AttributeError BEFORE the
    # re-ask, skipping the retry the docstring promises. Malformed shapes belong on the re-ask path.
    replies = iter(
        [
            '{"verdicts": {"1": true}}',  # an object, not a list
            '{"verdicts": [{"id": 1, "supported": true}, "junk"]}',  # a bare string among the verdicts
        ]
    )
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return next(replies)

    monkeypatch.setattr(ts, "_run_sonnet", fake)
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    with pytest.raises(ValueError):
        ts.audit_whats_new(wn, ARTS)
    assert len(calls) == 2


def test_audit_whats_new_treats_every_stray_element_the_same_way(monkeypatch):
    # One rule, no carve-outs. An earlier draft accepted a bare string beside a complete verdict
    # set while rejecting a duplicate id -- acceptance turned on whether the STRAY happened to
    # parse, not on whether the claims were covered.
    monkeypatch.setattr(ts, "_run_sonnet", lambda *a, **k: '{"verdicts": [{"id": 1, "supported": false}, "junk"]}')
    with pytest.raises(ValueError):
        ts.audit_whats_new([{"fact": "a", "sources": ["A1"]}], ARTS)


def test_audit_whats_new_takes_the_models_own_correction_from_a_two_object_reply(monkeypatch):
    """The measured run-271 shape, and the reason a re-ask alone was aimed at the wrong term.

    Replaying that run's real audit prompts, 3 of 36 replies contained more than one JSON object:
    the model wrote a malformed answer, noticed, and rewrote it. `_parse_json` reads only the
    first, so the correct answer sitting in the same reply was thrown away and the audit was
    charged a fail-open -- or, with a re-ask, a second billed call it did not need.
    """
    reply = (
        '{"verdicts": [{"id": 1, "supported": false}, {"id": 1, "supported": true}, '
        '{"id": 2, "supported": true}]}\n\n'
        "I need to redo this properly with one verdict per claim (1-2):\n\n"
        '{"verdicts": [{"id": 1, "supported": true}, {"id": 2, "supported": false}]}'
    )
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return reply

    monkeypatch.setattr(ts, "_run_sonnet", fake)
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    assert ts.audit_whats_new(wn, ARTS) == [True, False]
    assert len(calls) == 1  # no re-ask: the answer was already in the reply


def test_audit_whats_new_reads_an_unreadable_supported_as_not_supported(monkeypatch):
    """`0` and `null` must keep DROPPING the fact.

    Before this hardening `bool(0)` and `bool(None)` were False, so an unreadable verdict dropped
    its fact -- the safe direction. Rejecting the whole reply instead would fail open and SHIP the
    fact the auditor was trying to reject, which is the opposite of what this audit is for.
    """
    monkeypatch.setattr(
        ts,
        "_run_sonnet",
        lambda *a, **k: '{"verdicts": [{"id": 1, "supported": 0}, {"id": 2, "supported": null}]}',
    )
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    assert ts.audit_whats_new(wn, ARTS) == [False, False]


def test_audit_whats_new_raises_when_verdicts_miss_a_claim(monkeypatch):
    # A malformed audit (0-indexed ids here) leaves the last claim with no explicit verdict. It must
    # RAISE -- so synthesize_threads counts it + fails open LOUDLY -- rather than silently defaulting
    # that claim to supported, which would slip an unaudited (possibly fabricated) fact into the delta.
    monkeypatch.setattr(
        ts,
        "_run_sonnet",
        lambda *a, **k: '{"verdicts": [{"id": 0, "supported": false}, {"id": 1, "supported": false}]}',
    )
    wn = [{"fact": "a", "sources": ["A1"]}, {"fact": "b", "sources": ["A1"]}]
    with pytest.raises(ValueError):
        ts.audit_whats_new(wn, ARTS)


def test_synthesize_threads_audit_failure_is_fail_open_and_recorded(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])
    fake = {"whats_new": [{"fact": "kept despite audit error", "sources": ["A1"]}], "updated_narrative": "n"}

    def boom_audit(*a, **k):
        raise RuntimeError("audit endpoint down")

    out, failures = ts.synthesize_threads(
        [a], ARTS, run_id=2, store=store, synth_fn=lambda *args, **k: fake, audit_fn=boom_audit
    )
    assert failures == 1  # the authoritative in-memory count (drives the alert)
    # fail-open: the fact is kept (not erased) despite the audit error
    assert [f["fact"] for f in out[0]["whats_new"]] == ["kept despite audit error"]
    # ...and the failure is also recorded as durable health history
    row = store.conn.execute("SELECT threads_synthesized, audit_failures FROM thread_runs WHERE run_id=2").fetchone()
    assert row == (1, 1)


def test_run_sonnet_maps_raw_sdk_usage_to_run_usage_row(monkeypatch):
    # Regression: result.usage is the RAW SDK shape (input_tokens/...), must map to the
    # run_usage row keys. (The trial caught a KeyError here when raw usage was passed through.)
    from types import SimpleNamespace

    import claude_cli

    fake = SimpleNamespace(
        ok=True,
        text='{"ok": 1}',
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
        },
        total_cost_usd=0.01,
        duration_ms=1000,
    )

    async def fake_run_agent(*a, **k):
        return fake

    monkeypatch.setattr(claude_cli, "run_agent", fake_run_agent)
    rows: list[dict] = []
    ts._run_sonnet("u", "s", model="claude-sonnet-4-6", subagent="thread_synthesis", usage_rows=rows)
    assert rows == [
        {
            "subagent": "thread_synthesis",
            "model": "claude-sonnet-4-6",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_write_tokens": 2,
            "cache_read_tokens": 3,
            "api_cost_usd": 0.01,
            "duration_ms": 1000,
        }
    ]


def test_synthesize_threads_threads_usage_rows_through(store):
    # usage_rows must reach both the synth and audit calls so B's spend lands in run_usage.
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])

    def synth(*args, usage_rows=None, **k):
        usage_rows.append({"subagent": "thread_synthesis"})
        return {"whats_new": [{"fact": "x", "sources": ["A1"]}], "updated_narrative": "n"}

    def audit(*args, usage_rows=None, **k):
        usage_rows.append({"subagent": "thread_audit"})
        return [True]

    rows: list = []
    ts.synthesize_threads([a], ARTS, run_id=2, store=store, synth_fn=synth, audit_fn=audit, usage_rows=rows)
    assert [r["subagent"] for r in rows] == ["thread_synthesis", "thread_audit"]


def test_synthesize_threads_records_zero_failures_on_clean_run(store):
    tid = _seed_thread(store)
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="g", article_ids=["A1", "A2"])
    ts.synthesize_threads(
        [a],
        ARTS,
        run_id=2,
        store=store,
        synth_fn=lambda *args, **k: {"whats_new": [], "updated_narrative": "n"},
        audit_fn=lambda *a, **k: [],
    )
    assert store.conn.execute("SELECT audit_failures FROM thread_runs WHERE run_id=2").fetchone()[0] == 0


def test_apply_installment_is_atomic_on_persist_failure(store, monkeypatch):
    # If a late write fails, a question resolved earlier in the unit must roll back (stay open).
    tid = _seed_thread(store, open_qs=["Will it hold?"])
    a = threads.ThreadAssignment(thread_id=tid, is_new=False, cluster_story="x", open_questions=["Will it hold?"])
    monkeypatch.setattr(
        store, "set_installment_content", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    installment = {
        "whats_new": [{"fact": "f", "sources": ["A1"]}],
        "resolved": [{"question": "Will it hold?", "how": "yes"}],
    }
    with pytest.raises(RuntimeError):
        ts.apply_installment(store, a, installment, supported=[True], run_id=2)
    status = store.conn.execute("SELECT status FROM thread_questions WHERE thread_id=?", (tid,)).fetchone()[0]
    assert status == "open"  # the resolve rolled back with the failed installment write
