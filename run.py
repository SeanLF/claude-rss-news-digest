#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS → Claude curation → Email delivery
"""

import argparse
import csv
import html
import json
import os
import re
import shutil
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
CLAUDE_INPUT_DIR = DATA_DIR / "claude_input"  # Intermediate files for Claude
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
    """Check internet connectivity."""
    try:
        req = urllib.request.Request(
            "https://www.google.com/generate_204",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"Internet check failed: {e}")
        return False


def validate_env(dry_run: bool = False):
    """Check required environment variables. Exit if missing."""
    # ANTHROPIC_API_KEY is optional - Claude CLI can use `claude login` for Pro subscription
    required = []
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

    log(f"Fetched {total} articles from {len(sources)} sources")
    return total


# =============================================================================
# Digest Generation
# =============================================================================

def estimate_tokens(text: str) -> int:
    """Estimate token count (~3.5 chars/token for mixed content)."""
    return len(text) // 3


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', '', text)  # Remove tags
    text = html.unescape(text)  # Decode &amp; etc
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text


MAX_TOKENS_PER_FILE = 10000  # Conservative limit for Claude Code file reading


def prepare_claude_input(sources: list[dict]) -> list[Path]:
    """Prepare CSV input files for Claude - split if too large."""
    # Clean and recreate input directory
    if CLAUDE_INPUT_DIR.exists():
        shutil.rmtree(CLAUDE_INPUT_DIR)
    CLAUDE_INPUT_DIR.mkdir(parents=True)

    # Get previous headlines for deduplication
    previous_headlines = get_previous_headlines(days=7)

    # Write previous headlines CSV
    headlines_file = CLAUDE_INPUT_DIR / "headlines.csv"
    with open(headlines_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["headline", "tier", "date"])
        for h in previous_headlines:
            writer.writerow([h.get("headline", ""), h.get("tier", ""), h.get("date", "")])

    # Write sources CSV
    sources_file = CLAUDE_INPUT_DIR / "sources.csv"
    with open(sources_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "bias", "perspective"])
        for s in sources:
            writer.writerow([s["id"], s["name"], s["bias"], s["perspective"]])

    # Collect all articles
    all_articles = []
    for source in sources:
        source_file = FETCHED_DIR / f"{source['id']}.json"
        if source_file.exists():
            with open(source_file) as sf:
                articles = json.load(sf)
            for a in articles:
                summary = strip_html(a.get("summary") or "")[:200]
                title = strip_html(a.get("title") or "")
                all_articles.append([
                    source["id"],
                    title,
                    a.get("url", ""),
                    a.get("published", ""),
                    summary
                ])

    # Split articles into multiple files if needed
    article_files = []
    current_file_num = 1
    current_rows = []
    current_tokens = 0
    header = ["source_id", "title", "url", "published", "summary"]

    for row in all_articles:
        row_text = ",".join(str(x) for x in row)
        row_tokens = estimate_tokens(row_text)

        if current_tokens + row_tokens > MAX_TOKENS_PER_FILE and current_rows:
            # Write current file and start new one
            file_path = CLAUDE_INPUT_DIR / f"articles_{current_file_num}.csv"
            with open(file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(current_rows)
            article_files.append(file_path)
            current_file_num += 1
            current_rows = []
            current_tokens = 0

        current_rows.append(row)
        current_tokens += row_tokens

    # Write final file
    if current_rows:
        file_path = CLAUDE_INPUT_DIR / f"articles_{current_file_num}.csv"
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(current_rows)
        article_files.append(file_path)

    log(f"Prepared CSV input: {len(previous_headlines)} headlines, {len(all_articles)} articles in {len(article_files)} file(s)")
    return article_files


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
    """Run Claude to generate digest with streaming output."""
    log("Generating digest with Claude...")
    process = subprocess.Popen(
        ["claude", "--print", "--permission-mode", "acceptEdits", "/news-digest"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1  # Line buffered
    )
    # Stream output in real-time
    for line in process.stdout:
        print(line, end="", flush=True)
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Claude failed with code {process.returncode}")


def find_latest_digest() -> Path | None:
    """Find most recent digest file (HTML or TXT)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
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
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)  # Separate From address (for iCloud etc)
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")  # Display name for From/Subject
    recipients = [e.strip() for e in os.environ["DIGEST_EMAIL"].split(",")]

    content = digest_path.read_text()
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    is_html = digest_path.suffix == ".html"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{digest_name} – {date_str}"
    msg["From"] = f"{digest_name} <{smtp_from}>"
    msg["To"] = ", ".join(recipients)

    if is_html:
        msg.attach(MIMEText(content, "html", "utf-8"))
    else:
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
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)
    recipients = [e.strip() for e in os.environ["DIGEST_EMAIL"].split(",")]

    msg = MIMEText("This is a test email from News Digest.\n\nIf you received this, your SMTP config is working.", "plain", "utf-8")
    msg["Subject"] = "News Digest - Test Email"
    msg["From"] = smtp_from
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

    digest = find_latest_digest()
    if not digest:
        log("ERROR: No digest generated")
        return 1

    if args.dry_run:
        log(f"DRY RUN: Would send {digest.name}")
        # Don't record to DB on dry run - would break deduplication
    else:
        # Only record shown headlines and run on actual send
        shown_headlines = read_shown_headlines()
        record_shown_headlines(shown_headlines)
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
