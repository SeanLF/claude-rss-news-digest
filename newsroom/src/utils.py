"""Shared utilities for the news digest pipeline."""

import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from config import DATA_DIR, LOG_FILE

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging: stdout for terminal/systemd + rotating file."""
    DATA_DIR.mkdir(exist_ok=True)

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda *_: datetime.now(UTC).timetuple()

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=100_000, backupCount=1)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


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
