"""Tests for the --test-send QA path in run.py.

--test-send exists to deliver a single rendered digest to an arbitrary address
for email-client QA via Resend's single-recipient Emails.send. It must NEVER
create a broadcast, touch the audience, or record a run. --dry-run must render +
prepare the HTML but stop short of the actual send.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import run


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_FROM", "d@example.dev")
    monkeypatch.setenv("DIGEST_NAME", "Sean's Digest")


@pytest.fixture
def latest_selections(tmp_path, monkeypatch):
    """The latest run's selections.json that the no-``--selections`` fallback renders via MJML.

    A resolved fixture (sources carry name/url/bias, no bare article_id) so resolve_article_ids
    is a no-op and no article_index.json is needed.
    """
    import shutil

    ci = tmp_path / "claude_input"
    ci.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "kitchensink_selections.json", ci / "selections.json")
    # The fallback requires the sibling index (real selections carry opaque ids); kitchensink
    # is already resolved so an empty index satisfies the guard and resolve is a no-op.
    (ci / "article_index.json").write_text("{}")
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", ci)
    return ci / "selections.json"


def _guard_no_audience(monkeypatch):
    """Trip loudly if the test-send path reaches any Broadcasts/audience API."""
    import broadcast

    def forbidden(*a, **k):
        raise AssertionError("test-send must never touch the Broadcasts/audience flow")

    monkeypatch.setattr(broadcast, "send_broadcast", forbidden)
    monkeypatch.setattr(broadcast.resend.Broadcasts, "create", forbidden)
    monkeypatch.setattr(broadcast.resend.Broadcasts, "send", forbidden)
    monkeypatch.setattr(broadcast.resend.Contacts, "list", forbidden)


class TestParseAddrs:
    def test_flattens_repeated_and_comma_separated(self):
        assert run._parse_test_send_addrs(["a@x.com,b@x.com", "c@x.com"]) == [
            "a@x.com",
            "b@x.com",
            "c@x.com",
        ]

    def test_dedupes_preserving_order_and_strips(self):
        assert run._parse_test_send_addrs([" a@x.com , b@x.com ", "a@x.com"]) == ["a@x.com", "b@x.com"]

    def test_drops_empties(self):
        assert run._parse_test_send_addrs(["", " , ", "a@x.com"]) == ["a@x.com"]


def test_dry_run_renders_but_never_sends(monkeypatch, latest_selections, capsys):
    """--test-send --dry-run reaches the MJML render (render_email) but performs no send."""
    _guard_no_audience(monkeypatch)
    sends = []
    monkeypatch.setattr(run, "send_test_digest", lambda *a, **k: sends.append(a) or "should_not_be_called")

    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "qa@example.com", "--dry-run"])
    rc = run.main()

    assert rc == 0
    assert sends == []  # dry-run must NOT call the sender
    out = capsys.readouterr().out
    assert "would send test digest to qa@example.com" in out
    assert "no send performed" in out


def test_real_send_calls_emails_per_address_not_broadcast(monkeypatch, latest_selections, capsys):
    """Without --dry-run each address gets one Emails.send via send_test_digest;
    the audience/broadcast flow is never reached, and the MJML render is passed through."""
    _guard_no_audience(monkeypatch)
    calls = []

    def fake_send(html, addr, **kwargs):
        calls.append((html, addr))
        return f"email_{len(calls)}"

    monkeypatch.setattr(run, "send_test_digest", fake_send)

    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "a@x.com,b@x.com"])
    rc = run.main()

    assert rc == 0
    assert [addr for _, addr in calls] == ["a@x.com", "b@x.com"]
    # HTML handed to the sender is the MJML render (render_email): Outlook ghost tables
    # (mso conditionals) prove it went through MJML, and both addresses get the same render.
    sent = calls[0][0]
    assert "<html" in sent.lower() and "mso" in sent.lower()
    assert calls[0][0] == calls[1][0]  # rendered once, sent to each address
    out = capsys.readouterr().out
    assert "Sent test digest to a@x.com: email_1" in out
    assert "Sent test digest to b@x.com: email_2" in out


def test_no_selections_available_fails_loud(tmp_path, monkeypatch):
    """No selections.json and no --selections -> raise rather than silently sending nothing."""
    _guard_no_audience(monkeypatch)
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", tmp_path)  # empty dir -> no selections.json
    monkeypatch.setattr(run, "send_test_digest", lambda *a, **k: pytest.fail("should not send"))
    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "qa@example.com", "--dry-run"])
    with pytest.raises(FileNotFoundError):
        run.main()


def test_fallback_without_article_index_fails_loud(tmp_path, monkeypatch):
    """selections.json present but no sibling article_index.json -> raise rather than ship
    blank sources (the ids can't resolve). An explicit --selections is exempt (may be resolved)."""
    _guard_no_audience(monkeypatch)
    ci = tmp_path / "claude_input"
    ci.mkdir()
    (ci / "selections.json").write_text('{"must_know": [], "should_know": []}')  # no article_index.json
    monkeypatch.setattr(run, "CLAUDE_INPUT_DIR", ci)
    monkeypatch.setattr(run, "send_test_digest", lambda *a, **k: pytest.fail("should not send"))
    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "qa@example.com", "--dry-run"])
    with pytest.raises(FileNotFoundError):
        run.main()
