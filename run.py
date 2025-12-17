#!/usr/bin/env python3
"""Main entry point for news digest pipeline."""

import os
import sys
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Paths
APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
LOG_FILE = DATA_DIR / "digest.log"
DB_PATH = DATA_DIR / "digest.db"
OUTPUT_DIR = DATA_DIR / "output"


def log(message: str):
    """Log message with UTC timestamp to file and stdout."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line)
    DATA_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def check_internet() -> bool:
    """Check if we can reach the Anthropic API."""
    try:
        urllib.request.urlopen("https://api.anthropic.com", timeout=10)
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


def init_db():
    """Initialize database if it doesn't exist."""
    if DB_PATH.exists():
        return
    log("Initializing database...")
    # Import here to avoid circular deps and keep init_db.py standalone
    from init_db import init_db as do_init
    do_init()


def fetch_feeds():
    """Fetch RSS feeds."""
    log("Fetching RSS feeds...")
    result = subprocess.run(
        [sys.executable, APP_DIR / "fetch_feeds.py"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        log(f"Feed fetch failed: {result.stderr}")
        raise RuntimeError("Feed fetch failed")
    log(f"Fetched feeds: {result.stdout.strip()}")


def generate_digest():
    """Run Claude to generate the digest."""
    log("Generating digest with Claude...")
    result = subprocess.run(
        ["claude", "--print", "-p", "/news-digest"],
        capture_output=False  # Let Claude output flow through
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude failed with code {result.returncode}")


def find_latest_digest() -> Path | None:
    """Find the most recently created digest file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None


def send_email(digest_path: Path):
    """Send the digest via email."""
    log(f"Sending digest: {digest_path.name}")
    from send_email import send_digest
    send_digest(str(digest_path))
    log("Digest sent successfully")


def main():
    """Run the full digest pipeline."""
    # Check internet
    if not check_internet():
        log("No internet connection, skipping digest")
        return 0

    # Initialize DB if needed
    init_db()

    # Fetch feeds
    fetch_feeds()

    # Generate digest with Claude
    generate_digest()

    # Find and send the digest
    digest = find_latest_digest()
    if not digest:
        log("ERROR: No digest generated")
        return 1

    send_email(digest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)
