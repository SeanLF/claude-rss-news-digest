"""healthcheck.ping is a best-effort external dead-man's-switch signal.

Two hard contracts: (1) it is a no-op when HEALTHCHECK_PING_URL is unset, so
local dev / CI / dry-runs never ping; (2) it NEVER raises -- a monitoring ping
failing must not break or delay the digest itself.
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import healthcheck


class TestPing:
    def test_noop_without_env(self, monkeypatch):
        monkeypatch.delenv("HEALTHCHECK_PING_URL", raising=False)
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.ping()
            healthcheck.ping("start")
        urlopen.assert_not_called()

    def test_success_pings_base_url(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.ping()
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://hc-ping.com/abc"

    def test_event_appends_suffix(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc/")  # trailing slash tolerated
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.ping("fail")
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://hc-ping.com/abc/fail"

    def test_swallows_network_error(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
        with mock.patch("healthcheck.urllib.request.urlopen", side_effect=OSError("boom")):
            healthcheck.ping("start")  # must not raise

    def test_rejects_non_https(self, monkeypatch):
        # A misconfigured http:// URL must not be fetched (avoids leaking the
        # ping token over cleartext) -- treated like any other failure: no-op.
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "http://evil.example/abc")
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.ping()
        urlopen.assert_not_called()


class TestStageProgressLog:
    """Run 281 hung 62 minutes and NOTHING outside the container noticed: the deadman fires
    2h35m after start and OnFailure only at the 5h TimeoutStartSec, while run_health is
    structurally post-hoc -- it only judges runs that FINISH. A per-stage /log ping is the
    missing progress signal, and no workflow engine supplies it either."""

    def test_log_posts_the_stage_to_the_log_endpoint(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.log("cluster done in 200s")
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://hc-ping.com/abc/log"
        assert req.data == b"cluster done in 200s"

    def test_log_is_a_noop_without_env(self, monkeypatch):
        monkeypatch.delenv("HEALTHCHECK_PING_URL", raising=False)
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.log("cluster")
        urlopen.assert_not_called()

    def test_log_swallows_network_error(self, monkeypatch):
        """A progress ping must never delay or break the digest -- the whole point is that it
        is observability on a run that is otherwise fine."""
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
        with mock.patch("healthcheck.urllib.request.urlopen", side_effect=OSError("boom")):
            healthcheck.log("cluster")  # must not raise

    def test_log_refuses_cleartext(self, monkeypatch):
        """The URL embeds a secret token; the body names internal stages."""
        monkeypatch.setenv("HEALTHCHECK_PING_URL", "http://hc-ping.com/abc")
        with mock.patch("healthcheck.urllib.request.urlopen") as urlopen:
            healthcheck.log("cluster")
        urlopen.assert_not_called()
