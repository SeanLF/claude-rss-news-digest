"""Tests for run._deliver idempotent broadcast (2026-06-16 double-send guard).

_deliver is the single choke point that decides whether to actually send today's
digest. These tests pin that it never sends twice: not when the DB already shows
delivery, and not when a prior attempt created a broadcast whose delivery was
never confirmed (the exact gap that could double-mail subscribers on a resume).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import broadcast
import run


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_FROM", "d@example.dev")
    monkeypatch.setenv("RESEND_AUDIENCE_ID", "aud_1")


def _digest(tmp_path):
    p = tmp_path / "digest-2026-06-16-1200Z.html"
    p.write_text("<html><body>x</body></html>")
    return p


def _no_send(monkeypatch):
    """Patch send_broadcast to count calls and fail the test's intent if used."""
    calls = {"n": 0}

    def fake(*_a, **_k):
        calls["n"] += 1
        return broadcast.BroadcastResult(2, "bc_new", "sent")

    monkeypatch.setattr(run, "send_broadcast", fake)
    return calls


def test_deliver_skips_when_db_shows_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(run.db, "get_broadcast", lambda _date: ("bc_old", "sent"))
    # A skip reports the digest's real recipient count, not 0 (which reads as
    # "delivered to nobody"). The count lives on the date-keyed digests row.
    monkeypatch.setattr(run.db, "broadcast_recipients", lambda _date: 42, raising=False)
    sends = _no_send(monkeypatch)
    assert run._deliver(_digest(tmp_path), "<html>email</html>") == 42
    assert sends["n"] == 0


def test_deliver_reprobes_uncertain_broadcast_and_skips_if_delivered(monkeypatch, tmp_path):
    """A prior attempt created a broadcast but never confirmed delivery in the DB
    (status 'created'). _deliver must re-probe Resend and, if it was actually
    delivered, NOT create a second broadcast."""
    monkeypatch.setattr(run.db, "get_broadcast", lambda _date: ("bc_old", "created"))
    recorded = {}
    monkeypatch.setattr(run.db, "record_broadcast", lambda _d, bid, s: recorded.update(id=bid, status=s))
    monkeypatch.setattr(run.db, "broadcast_recipients", lambda _date: 7, raising=False)
    monkeypatch.setattr(run, "probe_status", lambda _bid: "sent", raising=False)
    monkeypatch.setattr(run, "resend_existing", lambda _bid: "sent", raising=False)
    sends = _no_send(monkeypatch)
    assert run._deliver(_digest(tmp_path), "<html>email</html>") == 7  # reports the prior delivery's count, not 0
    assert sends["n"] == 0, "must not create a second broadcast when the old one delivered"
    assert recorded == {"id": "bc_old", "status": "sent"}


def test_deliver_resends_existing_draft_when_not_delivered(monkeypatch, tmp_path):
    """A prior attempt created a broadcast that genuinely never sent (still draft).
    _deliver re-sends that SAME draft -- it must not create a fresh broadcast."""
    monkeypatch.setattr(run.db, "get_broadcast", lambda _date: ("bc_old", "created"))
    recorded = {}
    monkeypatch.setattr(run.db, "record_broadcast", lambda _d, bid, s: recorded.update(id=bid, status=s))
    monkeypatch.setattr(run, "probe_status", lambda _bid: "draft", raising=False)
    resent = {"id": None}
    monkeypatch.setattr(run, "resend_existing", lambda bid: resent.__setitem__("id", bid) or "sent", raising=False)
    sends = _no_send(monkeypatch)
    run._deliver(_digest(tmp_path), "<html>email</html>")
    assert sends["n"] == 0, "must reuse the existing draft, not create a new broadcast"
    assert resent["id"] == "bc_old"
    assert recorded == {"id": "bc_old", "status": "sent"}


def test_require_article_index_raises_when_missing(tmp_path):
    """--resume must refuse if article_index.json is gone: without it,
    resolve_article_ids silently leaves {article_id} placeholders and a broken
    digest would be broadcast. Fail loud instead."""
    import pytest as _pytest

    with _pytest.raises(FileNotFoundError):
        run._require_article_index(tmp_path)


def test_require_article_index_ok_when_present(tmp_path):
    (tmp_path / "article_index.json").write_text("{}")
    run._require_article_index(tmp_path)  # must not raise


# --- Sending requires recording (idempotency + dedup live in the DB) ----------


def test_broadcast_without_record_is_refused():
    """--no-record while still emailing has no double-send / dedup protection
    (both are DB-backed), so it must be refused unless explicitly forced."""
    import pytest as _pytest

    with _pytest.raises(SystemExit):
        run._require_recording_to_broadcast(skip_email=False, skip_record=True, force=False)


def test_broadcast_with_record_is_allowed():
    run._require_recording_to_broadcast(skip_email=False, skip_record=False, force=False)


def test_no_record_allowed_when_not_emailing():
    run._require_recording_to_broadcast(skip_email=True, skip_record=True, force=False)


def test_force_overrides_broadcast_without_record():
    run._require_recording_to_broadcast(skip_email=False, skip_record=True, force=True)


# --- Resume refuses stale (prior-day) artifacts ------------------------------


def test_require_fresh_artifacts_raises_when_stale(tmp_path):
    """Resume must not ship a prior day's curation under today's date."""
    import os
    import time

    import pytest as _pytest

    idx = tmp_path / "article_index.json"
    idx.write_text("{}")
    old = time.time() - 10 * 86400  # 10 days ago
    os.utime(idx, (old, old))
    with _pytest.raises(RuntimeError):
        run._require_fresh_artifacts(tmp_path)


def test_require_fresh_artifacts_ok_when_today(tmp_path):
    idx = tmp_path / "article_index.json"
    idx.write_text("{}")  # mtime defaults to now -> today UTC
    run._require_fresh_artifacts(tmp_path)  # must not raise


# --- Resume runs the same downstream steps as a normal run -------------------
# The full pipeline snapshots artifacts + processes threads after assembly; a
# resumed run must do the SAME (it used to skip both). Shared helper so the two
# paths can't drift again. Resume = continue the failed run through every step,
# not a lossy shortcut to force a send.


def _ok(record, label):
    """A monkeypatched archive_* that records it ran and reports success (True)."""
    record.append(label)
    return True


def test_archive_run_and_threads_runs_full_sequence(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", tmp_path)
    (tmp_path / "clusters.json").write_text('{"clusters": []}')
    order = []
    monkeypatch.setattr(run.db, "archive_selections", lambda j: _ok(order, "selections"))
    monkeypatch.setattr(run.db, "archive_clusters", lambda j: _ok(order, "clusters"))
    monkeypatch.setattr(run.db, "archive_run_artifacts", lambda d, models=None: _ok(order, "artifacts"))
    monkeypatch.setattr(run, "_process_story_threads", lambda: order.append("threads"))

    run._archive_run_and_threads('{"must_know": []}', model="claude-x")

    assert order == ["selections", "clusters", "artifacts", "threads"]


def test_archive_run_and_threads_skips_clusters_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", tmp_path)  # no clusters.json written
    calls = []
    monkeypatch.setattr(run.db, "archive_selections", lambda j: _ok(calls, "selections"))
    monkeypatch.setattr(run.db, "archive_clusters", lambda j: _ok(calls, "clusters"))
    monkeypatch.setattr(run.db, "archive_run_artifacts", lambda d, models=None: _ok(calls, "artifacts"))
    monkeypatch.setattr(run, "_process_story_threads", lambda: calls.append("threads"))

    run._archive_run_and_threads("{}", model=None)

    assert "clusters" not in calls
    assert calls == ["selections", "artifacts", "threads"]


def test_archival_failure_alerts_but_does_not_block(monkeypatch, tmp_path):
    """A failed archival write is fail-soft -- the helper returns normally so the
    send is never blocked -- but must not be silent: on the alerting path it fires
    an alert naming the failed step(s)."""
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", tmp_path)
    monkeypatch.setattr(run.db, "archive_selections", lambda j: False)  # write failed
    monkeypatch.setattr(run.db, "archive_run_artifacts", lambda d, models=None: True)
    monkeypatch.setattr(run, "_process_story_threads", lambda: None)
    monkeypatch.setattr(run.db, "should_alert", lambda: True)
    monkeypatch.setattr(run.db, "current_run_id", lambda: 229)
    alerted = {}
    monkeypatch.setattr(run, "send_archival_alert", lambda steps, rid: alerted.update(steps=steps, rid=rid))

    run._archive_run_and_threads("{}", model=None)  # must NOT raise

    assert alerted == {"steps": ["selections"], "rid": 229}


def test_helper_adds_no_error_swallow(monkeypatch, tmp_path):
    """The helper wraps nothing in its own try/except: an exception raised by a
    step propagates and short-circuits the rest. (The real db.archive_* funcs are
    fail-soft internally -- they catch their own sqlite errors and log -- so in
    production a DB hiccup does NOT abort the run; this pins that the helper
    itself never hides a propagating error, keeping resume==main semantics.)"""
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", tmp_path)

    def boom(_j):
        raise RuntimeError("unexpected non-sqlite error")

    monkeypatch.setattr(run.db, "archive_selections", boom)
    threads_ran = []
    monkeypatch.setattr(run, "_process_story_threads", lambda: threads_ran.append(1))

    with pytest.raises(RuntimeError, match="unexpected non-sqlite error"):
        run._archive_run_and_threads("{}", model=None)

    assert not threads_ran  # propagated before threads -- helper adds no swallow
