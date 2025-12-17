#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS → Claude curation → Email delivery
"""

import json
import os
import smtplib
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

# =============================================================================
# Configuration
# =============================================================================

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "digest.db"
LOG_FILE = DATA_DIR / "digest.log"
FETCHED_DIR = DATA_DIR / "fetched"
OUTPUT_DIR = DATA_DIR / "output"
SOURCES_FILE = APP_DIR / "sources.json"


def load_sources() -> list[dict]:
    """Load RSS sources from JSON file."""
    with open(SOURCES_FILE) as f:
        return json.load(f)


# =============================================================================
# Utilities
# =============================================================================

def log(message: str):
    """Log with UTC timestamp to stdout and file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    DATA_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def check_internet() -> bool:
    """Check internet connectivity via Cloudflare."""
    try:
        urllib.request.urlopen("http://1.1.1.1", timeout=5)
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


def validate_env():
    """Check required environment variables. Exit if missing."""
    required = ["ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASS", "DIGEST_EMAIL"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        log(f"ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


# =============================================================================
# Database
# =============================================================================

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    timezone TEXT,
    articles_fetched INTEGER,
    narratives_presented INTEGER
);

CREATE TABLE IF NOT EXISTS shown_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT NOT NULL,
    tier TEXT,
    shown_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shown_narratives_date ON shown_narratives(shown_at);
"""


def init_db():
    """Initialize database if it doesn't exist."""
    if DB_PATH.exists():
        return
    log("Initializing database...")
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DB_SCHEMA)
    conn.commit()
    conn.close()


def get_last_run_time() -> datetime | None:
    """Get timestamp of last digest run."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs")
        result = cursor.fetchone()[0]
        conn.close()
        if result:
            return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


# =============================================================================
# RSS Fetching
# =============================================================================

def parse_date(date_str: str | None) -> datetime | None:
    """Parse RSS date formats."""
    if not date_str:
        return None
    try:
        if "T" in date_str:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None


def fetch_source(source: dict, timeout: int = 15) -> tuple[str, list[dict]]:
    """Fetch single RSS source. Returns (source_id, articles)."""
    source_id = source["id"]
    try:
        req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
        feed = feedparser.parse(data)

        if feed.bozo and not feed.entries:
            return source_id, []

        articles = []
        for entry in feed.entries:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                try:
                    pub_str = datetime(*published[:6], tzinfo=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    pub_str = entry.get("published") or entry.get("updated")
            else:
                pub_str = entry.get("published") or entry.get("updated")

            article = {
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "published": pub_str,
                "summary": (entry.get("summary") or entry.get("description") or "")[:500],
            }
            if article["title"] and article["url"]:
                articles.append(article)

        print(f"  [{source_id}] {len(articles)} articles", flush=True)
        return source_id, articles

    except Exception as e:
        print(f"  [{source_id}] Error: {e}", flush=True)
        return source_id, []


def fetch_feeds(sources: list[dict]):
    """Fetch all RSS feeds in parallel."""
    log(f"Fetching {len(sources)} RSS feeds...")

    last_run = get_last_run_time()
    if last_run:
        print(f"  Filtering after: {last_run.isoformat()}", flush=True)

    FETCHED_DIR.mkdir(parents=True, exist_ok=True)
    for f in FETCHED_DIR.glob("*.json"):
        f.unlink()

    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_source, s): s for s in sources}
        for future in as_completed(futures):
            source_id, articles = future.result()
            results[source_id] = articles

    # Filter by date and save
    total = 0
    for source in sources:
        source_id = source["id"]
        articles = results.get(source_id, [])
        if last_run:
            articles = [a for a in articles if not parse_date(a.get("published")) or parse_date(a.get("published")) > last_run]

        with open(FETCHED_DIR / f"{source_id}.json", "w") as f:
            json.dump(articles, f, indent=2)
        total += len(articles)

    # Save metadata
    metadata = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources_count": len(sources),
        "total_articles": total,
        "sources": {s["id"]: {"name": s["name"], "bias": s["bias"], "perspective": s["perspective"]} for s in sources},
    }
    with open(FETCHED_DIR / "_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    log(f"Fetched {total} articles from {len(sources)} sources")


# =============================================================================
# Digest Generation
# =============================================================================

def generate_digest():
    """Run Claude to generate digest."""
    log("Generating digest with Claude...")
    result = subprocess.run(["claude", "--print", "-p", "/news-digest"])
    if result.returncode != 0:
        raise RuntimeError(f"Claude failed with code {result.returncode}")


def find_latest_digest() -> Path | None:
    """Find most recent digest file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None


# =============================================================================
# Email
# =============================================================================

def send_email(digest_path: Path):
    """Send digest via SMTP."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    recipients = [e.strip() for e in os.environ["DIGEST_EMAIL"].split(",")]

    content = digest_path.read_text()
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"News Digest - {date_str}"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(content, "plain", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())

    log(f"Sent to {', '.join(recipients)}")


# =============================================================================
# Main Pipeline
# =============================================================================

def main():
    """Run full digest pipeline."""
    validate_env()

    if not check_internet():
        log("No internet connection, skipping")
        return 0

    sources = load_sources()
    init_db()
    fetch_feeds(sources)
    generate_digest()

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
