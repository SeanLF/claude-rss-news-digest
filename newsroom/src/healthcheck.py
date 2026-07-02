"""Best-effort healthchecks.io pings -- an EXTERNAL dead-man's-switch.

The in-process ``--verify-today`` probe (run.py) runs on the same box via
systemd, so it cannot detect the failure modes where the box is down or the
timer never fires. healthchecks.io is off-box: it alerts when the expected
daily ping simply does not arrive within its schedule + grace window, catching
exactly that gap (cf. the 2026-06-16 silent-outage incident).

Reads HEALTHCHECK_PING_URL from the environment. When unset -- local dev, CI,
tests, dry-runs -- every call is a no-op, so pings fire only from the deployed
cron. All network/error paths are swallowed: a monitoring ping must NEVER break
or delay the digest itself.
"""

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PING_ENV = "HEALTHCHECK_PING_URL"
_TIMEOUT_S = 10


def ping(event: str | None = None) -> None:
    """Ping the configured healthchecks.io endpoint. No-op if unconfigured.

    ``event`` is None for a success ping, or "start"/"fail" for the run-started
    and run-failed signals. Never raises.
    """
    base = os.environ.get(_PING_ENV)
    if not base:
        return
    url = base.rstrip("/")
    if event:
        url = f"{url}/{event}"
    if not url.startswith("https://"):
        # Refuse cleartext: the ping URL embeds a secret token. A misconfigured
        # http:// value is a config error, not something to leak over the wire.
        logger.warning("HEALTHCHECK_PING_URL is not https -- skipping %s ping", event or "success")
        return
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "news-digest-healthcheck/1"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # nosec B310 -- https-guarded above
            resp.read()
    except (urllib.error.URLError, OSError) as e:
        logger.warning("healthcheck %s ping failed (non-fatal): %s", event or "success", e)
