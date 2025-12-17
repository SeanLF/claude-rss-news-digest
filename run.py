#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS → Claude curation → Email delivery
"""

import argparse
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

MAX_LOG_LINES = 1000  # Keep last N log lines


def load_sources() -> list[dict]:
    """Load and validate RSS sources from JSON file."""
    with open(SOURCES_FILE) as f:
        sources = json.load(f)

    # Validate schema
    required_keys = {"id", "name", "url", "bias", "perspective"}
    for i, source in enumerate(sources):
        missing = required_keys - set(source.keys())
        if missing:
            raise ValueError(f"sources.json[{i}] missing keys: {missing}")
        if not source["url"].startswith(("http://", "https://")):
            raise ValueError(f"sources.json[{i}] invalid URL: {source['url']}")

    return sources


# =============================================================================
# Utilities
# =============================================================================

def log(message: str):
    """Log with UTC timestamp to stdout and file (with rotation)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)

    DATA_DIR.mkdir(exist_ok=True)

    # Read existing lines, append new, keep last N
    lines = []
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
    lines.append(line)
    if len(lines) > MAX_LOG_LINES:
        lines = lines[-MAX_LOG_LINES:]
    LOG_FILE.write_text("\n".join(lines) + "\n")


def check_internet() -> bool:
    """Check internet connectivity via Cloudflare (HTTPS)."""
    try:
        urllib.request.urlopen("https://1.1.1.1", timeout=5)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"Internet check failed: {e}")
        return False


def validate_env(dry_run: bool = False):
    """Check required environment variables. Exit if missing."""
    required = ["ANTHROPIC_API_KEY"]
    if not dry_run:
        required.extend(["SMTP_USER", "SMTP_PASS", "DIGEST_EMAIL"])

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
    articles_fetched INTEGER,
    articles_emailed INTEGER
);

CREATE TABLE IF NOT EXISTS shown_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT NOT NULL,
    tier TEXT,
    shown_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shown_narratives_date ON shown_narratives(shown_at);
CREATE INDEX IF NOT EXISTS idx_digest_runs_date ON digest_runs(run_at);
"""


def init_db():
    """Initialize or migrate database."""
    DATA_DIR.mkdir(exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        # Create tables if they don't exist
        conn.executescript(DB_SCHEMA)

        # Migrate: add articles_emailed if missing (old schema had different columns)
        cursor = conn.execute("PRAGMA table_info(digest_runs)")
        columns = {row[1] for row in cursor.fetchall()}

        if "articles_emailed" not in columns:
            log("Migrating database: adding articles_emailed column...")
            conn.execute("ALTER TABLE digest_runs ADD COLUMN articles_emailed INTEGER DEFAULT 0")

        # Migrate: remove old unused columns by ignoring them (SQLite can't drop columns easily)
        # Old columns (timezone, narratives_presented) will just be ignored


def get_last_run_time() -> datetime | None:
    """Get timestamp of last digest run."""
    if not DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs")
            result = cursor.fetchone()[0]
            if result:
                return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except sqlite3.Error as e:
        log(f"DB error getting last run time: {e}")
    return None


def record_run(articles_fetched: int, articles_emailed: int = 0):
    """Record a successful digest run."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO digest_runs (articles_fetched, articles_emailed) VALUES (?, ?)",
                (articles_fetched, articles_emailed)
            )
        log(f"Recorded run: {articles_fetched} fetched, {articles_emailed} emailed")
    except sqlite3.Error as e:
        log(f"DB error recording run: {e}")


def get_previous_headlines(days: int = 7) -> list[dict]:
    """Get headlines shown in the last N days for deduplication."""
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("""
                SELECT headline, tier, date(shown_at) as date
                FROM shown_narratives
                WHERE shown_at > datetime('now', ?)
                ORDER BY shown_at DESC
            """, (f'-{days} days',))
            return [{"headline": row[0], "tier": row[1], "date": row[2]} for row in cursor.fetchall()]
    except sqlite3.Error as e:
        log(f"DB error getting previous headlines: {e}")
        return []


def record_shown_headlines(headlines: list[dict]):
    """Record headlines that were shown in this digest."""
    if not headlines:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier) VALUES (?, ?)",
                [(h.get("headline", ""), h.get("tier", "")) for h in headlines]
            )
        log(f"Recorded {len(headlines)} shown headlines")
    except sqlite3.Error as e:
        log(f"DB error recording headlines: {e}")


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
    except (ValueError, TypeError) as e:
        # Don't log - too noisy for date parsing
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
            log(f"[{source_id}] Feed parse error: {feed.bozo_exception}")
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

    except urllib.error.URLError as e:
        print(f"  [{source_id}] Network error: {e.reason}", flush=True)
        return source_id, []
    except TimeoutError:
        print(f"  [{source_id}] Timeout", flush=True)
        return source_id, []
    except Exception as e:
        print(f"  [{source_id}] Error: {type(e).__name__}: {e}", flush=True)
        return source_id, []


def fetch_feeds(sources: list[dict]) -> int:
    """Fetch all RSS feeds in parallel. Returns total article count."""
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
            # Parse date once, filter
            filtered = []
            for a in articles:
                pub_date = parse_date(a.get("published"))
                if pub_date is None or pub_date > last_run:
                    filtered.append(a)
            articles = filtered

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
    return total


# =============================================================================
# Digest Generation
# =============================================================================

def prepare_claude_input(sources: list[dict]) -> Path:
    """Prepare input.json for Claude with previous headlines only (articles stay in fetched/)."""
    # Get previous headlines for deduplication
    previous_headlines = get_previous_headlines(days=7)

    # Slim input - just dedup data, Claude reads articles from fetched/*.json
    input_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "previous_headlines": previous_headlines,
    }

    input_file = DATA_DIR / "input.json"
    with open(input_file, "w") as f:
        json.dump(input_data, f, indent=2)

    log(f"Prepared input.json: {len(previous_headlines)} previous headlines")
    return input_file


def read_shown_headlines() -> list[dict]:
    """Read shown_headlines.json output from Claude."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if not headlines_file.exists():
        log("Warning: shown_headlines.json not found")
        return []

    try:
        with open(headlines_file) as f:
            headlines = json.load(f)
        # Clean up after reading
        headlines_file.unlink()
        return headlines
    except (json.JSONDecodeError, IOError) as e:
        log(f"Error reading shown_headlines.json: {e}")
        return []


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

def send_email(digest_path: Path) -> int:
    """Send digest via SMTP. Returns number of recipients."""
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

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())
        log(f"Sent to {', '.join(recipients)}")
        return len(recipients)
    except smtplib.SMTPException as e:
        log(f"SMTP error: {e}")
        raise


# =============================================================================
# Main Pipeline
# =============================================================================

def send_test_email() -> int:
    """Send a test email to verify SMTP config."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    recipients = [e.strip() for e in os.environ["DIGEST_EMAIL"].split(",")]

    msg = MIMEText("This is a test email from News Digest.\n\nIf you received this, your SMTP config is working.", "plain", "utf-8")
    msg["Subject"] = "News Digest - Test Email"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())
        log(f"Test email sent to {', '.join(recipients)}")
        return 0
    except smtplib.SMTPException as e:
        log(f"SMTP error: {e}")
        return 1


def main():
    """Run full digest pipeline."""
    parser = argparse.ArgumentParser(description="News Digest Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and generate but don't email")
    parser.add_argument("--test-email", action="store_true", help="Send test email and exit")
    args = parser.parse_args()

    # Test email mode - just verify SMTP works
    if args.test_email:
        validate_env(dry_run=False)
        return send_test_email()

    validate_env(dry_run=args.dry_run)

    if not check_internet():
        log("No internet connection, skipping")
        return 0

    sources = load_sources()
    init_db()
    articles_fetched = fetch_feeds(sources)

    # Prepare input for Claude (articles + previous headlines)
    prepare_claude_input(sources)

    # Generate digest
    generate_digest()

    # Record shown headlines from Claude's output
    shown_headlines = read_shown_headlines()
    record_shown_headlines(shown_headlines)

    digest = find_latest_digest()
    if not digest:
        log("ERROR: No digest generated")
        return 1

    if args.dry_run:
        log(f"DRY RUN: Would send {digest.name}")
        record_run(articles_fetched, articles_emailed=0)
    else:
        recipients = send_email(digest)
        record_run(articles_fetched, articles_emailed=recipients)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted")
        sys.exit(130)
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        sys.exit(1)
