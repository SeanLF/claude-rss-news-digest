"""Tests for broadcast.py Resend delivery resilience.

The 2026-06-16 incident: Resend ACCEPTED the broadcast send (it reached status
'queued' and delivered) but the HTTP response read-timed-out, so send_broadcast
raised and the caller wiped the run. These tests pin the fix -- a send whose
response fails but whose broadcast is in an accepted state is treated as
delivered, while a send that genuinely did not dispatch still fails loudly.
"""

import sys
from pathlib import Path

import pytest
import resend

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import broadcast


def _resend_error(message: str = "Request failed: Read timed out"):
    return resend.exceptions.ResendError(
        code=500,
        error_type="application_error",
        message=message,
        suggested_action="retry",
    )


def _raise_timeout(_params):
    raise _resend_error()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_FROM", "d@example.dev")
    monkeypatch.setenv("DIGEST_NAME", "Sean's Digest")  # apostrophe is intentional
    monkeypatch.setenv("RESEND_AUDIENCE_ID", "aud_1")


@pytest.fixture
def digest_file(tmp_path):
    p = tmp_path / "digest-2026-06-16.html"
    p.write_text("<html>digest</html>")
    return p


def _patch_resend(monkeypatch, *, send, get=None):
    monkeypatch.setattr(broadcast.resend.Contacts, "list", lambda **kw: {"data": [{"id": "c1"}, {"id": "c2"}]})
    monkeypatch.setattr(broadcast.resend.Broadcasts, "create", lambda params: {"id": "bc_1"})
    monkeypatch.setattr(broadcast.resend.Broadcasts, "send", send)
    if get is not None:
        monkeypatch.setattr(broadcast.resend.Broadcasts, "get", get)


@pytest.mark.parametrize("accepted_status", ["queued", "sending", "sent"])
def test_send_response_failure_but_accepted_is_delivered(monkeypatch, digest_file, accepted_status):
    """A failed send response is delivery, not failure, if Resend accepted it."""
    _patch_resend(monkeypatch, send=_raise_timeout, get=lambda _id: {"status": accepted_status})
    # Must NOT raise; reports the audience as recipients and the verified status.
    result = broadcast.send_broadcast(digest_file, lambda html: html)
    assert result.recipients == 2
    assert result.broadcast_id == "bc_1"
    assert result.status == accepted_status


def test_send_failure_not_dispatched_raises(monkeypatch, digest_file):
    """If the broadcast never left 'draft', the timeout is a genuine failure."""
    _patch_resend(monkeypatch, send=_raise_timeout, get=lambda _id: {"status": "draft"})
    with pytest.raises(resend.exceptions.ResendError):
        broadcast.send_broadcast(digest_file, lambda html: html)


def test_send_failure_status_unreadable_raises(monkeypatch, digest_file):
    """If status can't be read, fail loud rather than assume delivery."""

    def get_fail(_id):
        raise _resend_error("status unavailable")

    _patch_resend(monkeypatch, send=_raise_timeout, get=get_fail)
    with pytest.raises(resend.exceptions.ResendError):
        broadcast.send_broadcast(digest_file, lambda html: html)


def test_happy_path_does_not_check_status(monkeypatch, digest_file):
    """The status probe is only paid on the failure path."""
    calls = {"get": 0}

    def get(_id):
        calls["get"] += 1
        return {"status": "sent"}

    _patch_resend(monkeypatch, send=lambda params: {"id": "bc_1"}, get=get)
    result = broadcast.send_broadcast(digest_file, lambda html: html)
    assert result.recipients == 2
    assert result.status == "sent"
    assert calls["get"] == 0


def test_send_broadcast_persists_id_before_sending(monkeypatch, digest_file):
    """on_created fires with the broadcast id BEFORE the send is attempted, so a
    send that fails still leaves the id persisted for a resume to recover."""
    events = []

    def send(_params):
        events.append("send")
        raise _resend_error()

    # 'draft' => genuine failure (not accepted), so send_broadcast re-raises.
    _patch_resend(monkeypatch, send=send, get=lambda _id: {"status": "draft"})
    with pytest.raises(resend.exceptions.ResendError):
        broadcast.send_broadcast(digest_file, lambda h: h, on_created=lambda bid: events.append(f"created:{bid}"))
    assert events == ["created:bc_1", "send"]


def test_resend_existing_sends_without_creating(monkeypatch):
    """resend_existing sends an already-created broadcast and never creates one."""
    creates = {"n": 0}
    monkeypatch.setattr(broadcast.resend.Broadcasts, "create", lambda p: creates.__setitem__("n", creates["n"] + 1))
    monkeypatch.setattr(broadcast.resend.Broadcasts, "send", lambda p: {"id": p["broadcast_id"]})
    assert broadcast.resend_existing("bc_old") == "sent"
    assert creates["n"] == 0


def test_resend_existing_accepts_on_timeout(monkeypatch):
    """A send-response failure on an already-queued broadcast is delivery."""
    monkeypatch.setattr(broadcast.resend.Broadcasts, "send", _raise_timeout)
    monkeypatch.setattr(broadcast.resend.Broadcasts, "get", lambda _id: {"status": "queued"})
    assert broadcast.resend_existing("bc_old") == "queued"


def test_resend_existing_raises_when_not_accepted(monkeypatch):
    monkeypatch.setattr(broadcast.resend.Broadcasts, "send", _raise_timeout)
    monkeypatch.setattr(broadcast.resend.Broadcasts, "get", lambda _id: {"status": "draft"})
    with pytest.raises(resend.exceptions.ResendError):
        broadcast.resend_existing("bc_old")
