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
    sends = _no_send(monkeypatch)
    assert run._deliver(_digest(tmp_path)) == 0
    assert sends["n"] == 0


def test_deliver_reprobes_uncertain_broadcast_and_skips_if_delivered(monkeypatch, tmp_path):
    """A prior attempt created a broadcast but never confirmed delivery in the DB
    (status 'created'). _deliver must re-probe Resend and, if it was actually
    delivered, NOT create a second broadcast."""
    monkeypatch.setattr(run.db, "get_broadcast", lambda _date: ("bc_old", "created"))
    recorded = {}
    monkeypatch.setattr(run.db, "record_broadcast", lambda _d, bid, s: recorded.update(id=bid, status=s))
    monkeypatch.setattr(run, "probe_status", lambda _bid: "sent", raising=False)
    monkeypatch.setattr(run, "resend_existing", lambda _bid: "sent", raising=False)
    sends = _no_send(monkeypatch)
    assert run._deliver(_digest(tmp_path)) == 0
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
    run._deliver(_digest(tmp_path))
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
