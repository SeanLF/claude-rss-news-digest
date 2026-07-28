"""Shared utilities for the news digest pipeline."""

import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from config import DATA_DIR, LOG_FILE

logger = logging.getLogger(__name__)

# A delimited group of opaque article IDs: "[A221]", "(A316)", "(A110, A263)",
# "(A316, A317 and A318)". Article ids are internal audit provenance -- they
# belong only in a story's separate ``sources`` field -- and must never reach
# reader-facing text or the thread's carried fact memory.
#
# It lives in a leaf module because the copies drift: the 2026-06-30 thread fix
# taught its own copy the bracketed form, and run 247 (2026-07-28) then leaked
# the parenthesised form past a second copy in merge.py. One definition instead.
#
# The id run accepts the joins a model actually writes, not just commas. But
# delimiters must MATCH, and bare "A316" is deliberately NOT matched: with no
# delimiter it cannot be told from legitimate text, and a false positive here
# silently edits what a reader sees.
_ID_RUN = r"A\d+(?:\s*(?:,|;|&|and)\s*A\d+)*"
ARTICLE_ID_GROUP = re.compile(rf"\s*(?:\[\s*{_ID_RUN}\s*\]|\(\s*{_ID_RUN}\s*\))")


def strip_article_ids(text: str) -> str:
    """Remove article-id groups from text, tidying the whitespace they leave.

    Numeric source markers like ``[1]`` are left alone -- only A-prefixed ids
    match. Returns the input byte-identical when nothing matched, so callers can
    use ``!=`` as the "did this leak?" test without cosmetic whitespace fixes
    masquerading as leaks in the logs.
    """
    if not text:
        return text
    stripped = ARTICLE_ID_GROUP.sub("", text)
    return re.sub(r"\s{2,}", " ", stripped).strip() if stripped != text else text


def setup_logging():
    """Configure logging: stdout for terminal/systemd + rotating file.

    Falls back to stdout-only when the data dir is read-only. The dead-man's
    switch mounts the data volume ``:ro`` and runs ``--verify-today``; a
    verify-only run must not crash (and so fail to alert) just because it
    cannot open the log file on a read-only mount.
    """
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda *_: datetime.now(UTC).timetuple()

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    try:
        DATA_DIR.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=100_000, backupCount=1)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        # Read-only data volume (deadman :ro mount) or other filesystem error:
        # keep stdout/journal logging rather than aborting the run.
        root.warning("File logging disabled (%s): %s", LOG_FILE, e)


def check_internet() -> bool:
    """Check internet connectivity."""
    try:
        req = urllib.request.Request("https://www.google.com/generate_204", headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=5)  # nosec B310
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning("Internet check failed: %s", e)
        return False


def validate_env(dry_run: bool = False):
    """Check required environment variables. Exit if missing."""
    required = []
    if not dry_run:
        required.extend(["RESEND_API_KEY", "RESEND_FROM", "RESEND_AUDIENCE_ID"])

    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        logger.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)

    # Alerting config is checked at second zero, not at alert time -- the one moment it
    # cannot be reported. HEALTH_ALERT_EMAIL was absent for months (terraform wrote
    # DIGEST_ALERT_EMAIL, which nothing reads) and the only symptom was a WARNING buried
    # inside runs that were already failing, so 18 days of a dead source went unseen.
    # NOT fatal: alerting is observability, and a digest must still ship without it.
    if not dry_run and not os.environ.get("HEALTH_ALERT_EMAIL"):
        logger.error(
            "HEALTH_ALERT_EMAIL is not set: source-health, archival and thread-audit alerts "
            "will be DROPPED this run. Digest continues; alerting is blind until this is set."
        )
