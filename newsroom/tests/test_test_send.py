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
def rendered_digest(tmp_path, monkeypatch):
    """A pre-rendered digest on disk that find_latest_digest returns."""
    digest = tmp_path / "digest-2026-07-05-0900Z.html"
    digest.write_text("<html><style>:root{--x:red}a{color:var(--x)}</style><body>Digest</body></html>")
    monkeypatch.setattr(run, "find_latest_digest", lambda: digest)
    return digest


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


def test_dry_run_renders_and_prepares_but_never_sends(monkeypatch, rendered_digest, capsys):
    """--test-send --dry-run reaches render + prepare_for_email but performs no send."""
    _guard_no_audience(monkeypatch)
    sends = []
    monkeypatch.setattr(run, "send_test_digest", lambda *a, **k: sends.append(a) or "should_not_be_called")

    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "qa@example.com", "--dry-run"])
    rc = run.main()

    assert rc == 0
    assert sends == []  # dry-run must NOT call the sender
    out = capsys.readouterr().out
    assert "would send test digest to qa@example.com" in out
    # prepare_for_email ran: the CSS var was resolved out of the reported HTML
    assert "no send performed" in out


def test_real_send_calls_emails_per_address_not_broadcast(monkeypatch, rendered_digest, capsys):
    """Without --dry-run each address gets one Emails.send via send_test_digest;
    the audience/broadcast flow is never reached, and prepared HTML is passed through."""
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
    # HTML handed to the sender is exactly prepare_for_email(file) -- prepared, not raw.
    from render import prepare_for_email

    expected_html = prepare_for_email(rendered_digest.read_text())
    assert calls[0][0] == expected_html
    assert calls[0][0] != rendered_digest.read_text()  # the file was transformed, not passed through
    out = capsys.readouterr().out
    assert "Sent test digest to a@x.com: email_1" in out
    assert "Sent test digest to b@x.com: email_2" in out


def test_no_digest_and_no_selections_fails_loud(monkeypatch):
    """With nothing to render, the path raises rather than silently sending nothing."""
    _guard_no_audience(monkeypatch)
    monkeypatch.setattr(run, "find_latest_digest", lambda: None)
    monkeypatch.setattr(run, "send_test_digest", lambda *a, **k: pytest.fail("should not send"))
    monkeypatch.setattr(sys, "argv", ["run.py", "--test-send", "qa@example.com", "--dry-run"])
    with pytest.raises(FileNotFoundError):
        run.main()
