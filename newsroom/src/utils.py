"""Shared utilities for the news digest pipeline."""

import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

from config import DATA_DIR, LOG_FILE, MAX_LOG_LINES


def log(message: str, level: str = "INFO"):
    """Log with UTC timestamp and level to stdout and file (with rotation)."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] [{level}] {message}"
    print(line, flush=True)

    DATA_DIR.mkdir(exist_ok=True)
    lines = []
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
    lines.append(line)
    if len(lines) > MAX_LOG_LINES:
        lines = lines[-MAX_LOG_LINES:]
    LOG_FILE.write_text("\n".join(lines) + "\n")


def check_internet() -> bool:
    """Check internet connectivity."""
    try:
        req = urllib.request.Request("https://www.google.com/generate_204", headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=5)  # nosec B310
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"Internet check failed: {e}", "WARN")
        return False


def validate_env(dry_run: bool = False):
    """Check required environment variables. Exit if missing."""
    required = []
    if not dry_run:
        required.extend(["RESEND_API_KEY", "RESEND_FROM", "RESEND_AUDIENCE_ID"])

    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        log(f"Missing environment variables: {', '.join(missing)}", "ERROR")
        sys.exit(1)
