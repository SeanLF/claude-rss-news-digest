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
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import resend

# =============================================================================
# Configuration
# =============================================================================

MAX_RETRIES = int(os.environ.get("RSS_MAX_RETRIES", "3"))  # Retry flaky RSS feeds
RETRY_DELAY = int(os.environ.get("RSS_RETRY_DELAY", "2"))  # Base delay in seconds (exponential backoff)
HEALTH_ALERT_THRESHOLD = int(os.environ.get("HEALTH_ALERT_THRESHOLD", "3"))  # Consecutive failures before alert

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "digest.db"
LOG_FILE = DATA_DIR / "digest.log"
FETCHED_DIR = DATA_DIR / "fetched"
OUTPUT_DIR = DATA_DIR / "output"
CLAUDE_INPUT_DIR = DATA_DIR / "claude_input"  # Intermediate files for Claude
SOURCES_FILE = APP_DIR / "sources.json"
STYLES_FILE = APP_DIR / "digest.css"

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
        # Prevent path traversal - source_id is used in file paths
        if not re.match(r'^[a-z0-9_]+$', source["id"]):
            raise ValueError(f"sources.json[{i}] invalid id '{source['id']}': must be lowercase alphanumeric/underscore only")

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
        required.extend(["RESEND_API_KEY", "RESEND_FROM", "RESEND_AUDIENCE_ID"])

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
    run_at DATETIME DEFAULT (datetime('now', 'utc')),
    articles_fetched INTEGER,
    articles_emailed INTEGER
);

CREATE TABLE IF NOT EXISTS shown_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT NOT NULL,
    tier TEXT,
    shown_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS source_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT,
    recorded_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS digests (
    date TEXT PRIMARY KEY,
    html TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE INDEX IF NOT EXISTS idx_shown_narratives_date ON shown_narratives(shown_at);
CREATE INDEX IF NOT EXISTS idx_digest_runs_date ON digest_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_source_health_source ON source_health(source_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(date);
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
            try:
                log("Migrating database: adding articles_emailed column...")
                conn.execute("ALTER TABLE digest_runs ADD COLUMN articles_emailed INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.Error as e:
                log(f"Migration failed: {e}")
                conn.rollback()
                raise

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


def save_digest(digest_path: Path):
    """Save digest HTML to database for web serving."""
    # Extract date from filename (digest-YYYY-MM-DD*.html -> YYYY-MM-DD)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', digest_path.stem)
    if match:
        date_str = match.group(1)
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log(f"WARNING: Could not extract date from '{digest_path.stem}', using {date_str}")

    html_content = digest_path.read_text()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO digests (date, html) VALUES (?, ?)",
                (date_str, html_content)
            )
        log(f"Saved digest to database: {date_str}")
    except sqlite3.Error as e:
        log(f"DB error saving digest: {e}")


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
    # Validate format before processing
    if headlines and not isinstance(headlines[0], dict):
        log(f"ERROR: shown_headlines.json has wrong format - expected list of dicts, got list of {type(headlines[0]).__name__}")
        log(f"First item: {repr(headlines[0][:100]) if isinstance(headlines[0], str) else repr(headlines[0])}")
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier) VALUES (?, ?)",
                [(h.get("headline", ""), h.get("tier", "")) for h in headlines]
            )
        log(f"Saved {len(headlines)} headlines to dedup history")
    except sqlite3.Error as e:
        log(f"DB error recording headlines: {e}")


def record_source_health(results: list[tuple[str, bool, str | None]]):
    """Record source fetch results. Each tuple is (source_id, success, error_message)."""
    if not results:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO source_health (source_id, success, error_message) VALUES (?, ?, ?)",
                results
            )
    except sqlite3.Error as e:
        log(f"DB error recording source health for {len(results)} sources: {e}")


def get_consecutive_failures(source_id: str, limit: int = 10) -> int:
    """Get count of consecutive recent failures for a source."""
    if not DB_PATH.exists():
        return 0
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("""
                SELECT success FROM source_health
                WHERE source_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
            """, (source_id, limit))
            count = 0
            for (success,) in cursor:
                if success:
                    break
                count += 1
            return count
    except sqlite3.Error as e:
        log(f"DB error getting consecutive failures for {source_id}: {e}")
        return 0


def get_failing_sources(min_consecutive: int = 3) -> list[tuple[str, int]]:
    """Get sources with N+ consecutive failures. Returns [(source_id, failure_count)]."""
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT source_id FROM source_health
                WHERE recorded_at > datetime('now', '-7 days')
            """)
            source_ids = [row[0] for row in cursor]
    except sqlite3.Error as e:
        log(f"DB error getting failing sources: {e}")
        return []

    failing = [
        (sid, count)
        for sid in source_ids
        if (count := get_consecutive_failures(sid)) >= min_consecutive
    ]
    return sorted(failing, key=lambda x: -x[1])


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


def fetch_source(source: dict, timeout: int = 15) -> tuple[str, list[dict], str | None]:
    """Fetch single RSS source with retry logic. Returns (source_id, articles, error_or_none)."""
    source_id = source["id"]
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
            feed = feedparser.parse(data)

            if feed.bozo and not feed.entries:
                # Parse error - don't retry, feed is malformed
                error_msg = f"Feed parse error: {feed.bozo_exception}"
                print(f"  [{source_id}] {error_msg}", flush=True)
                return source_id, [], error_msg

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
            return source_id, articles, None  # Success

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Transient errors - retry with exponential backoff
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2 ** attempt)  # 1s, 2s, 4s
                time.sleep(delay)
            continue
        except Exception as e:
            # Non-transient error - don't retry
            error_msg = f"{type(e).__name__}: {e}"
            print(f"  [{source_id}] Error: {error_msg}", flush=True)
            return source_id, [], error_msg

    # All retries exhausted
    error_msg = str(getattr(last_error, 'reason', last_error)) if last_error else "Unknown"
    print(f"  [{source_id}] Failed after {MAX_RETRIES} retries: {error_msg}", flush=True)
    return source_id, [], f"Failed after {MAX_RETRIES} retries: {error_msg}"


def fetch_feeds(sources: list[dict]) -> tuple[int, int]:
    """Fetch all RSS feeds in parallel. Returns (total_articles, failed_count)."""
    log(f"Fetching {len(sources)} RSS feeds...")

    last_run = get_last_run_time()
    if last_run:
        print(f"  Filtering after: {last_run.isoformat()}", flush=True)

    FETCHED_DIR.mkdir(parents=True, exist_ok=True)
    for f in FETCHED_DIR.glob("*.json"):
        f.unlink()

    results = {}
    health_records = []  # (source_id, success, error_message)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_source, s): s for s in sources}
        for future in as_completed(futures):
            source_id, articles, error = future.result()
            results[source_id] = articles
            health_records.append((source_id, error is None, error))

    # Record health to DB
    record_source_health(health_records)

    # Filter by date and save
    total = 0
    for source in sources:
        source_id = source["id"]
        articles = results.get(source_id, [])
        if last_run:
            articles = [
                a for a in articles
                if (pub := parse_date(a.get("published"))) is None or pub > last_run
            ]

        with open(FETCHED_DIR / f"{source_id}.json", "w") as f:
            json.dump(articles, f, indent=2)
        total += len(articles)

    # Health summary
    failed_this_run = [(sid, err) for sid, success, err in health_records if not success]
    succeeded = len(sources) - len(failed_this_run)
    log(f"Fetched {total} articles from {succeeded}/{len(sources)} sources")

    if failed_this_run:
        log(f"Failed sources this run: {', '.join(sid for sid, _ in failed_this_run)}")

    # Check for persistently failing sources
    persistently_failing = get_failing_sources(min_consecutive=HEALTH_ALERT_THRESHOLD)
    if persistently_failing:
        log(f"WARNING: Sources with {HEALTH_ALERT_THRESHOLD}+ consecutive failures: {', '.join(f'{sid}({n}x)' for sid, n in persistently_failing)}")

    return total, len(failed_this_run)


# =============================================================================
# Digest Generation
# =============================================================================

def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars/token for CSV with URLs)."""
    return len(text) // 4


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', '', text)  # Remove tags
    text = html.unescape(text)  # Decode &amp; etc
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text


def is_safe_url(url: str) -> bool:
    """Validate URL has a safe scheme (http/https only)."""
    return url.startswith(("http://", "https://"))


MAX_TOKENS_PER_FILE = 10000  # Conservative limit for Claude Code file reading
MAX_TITLE_LENGTH = 500  # Cap title length for safety
MAX_SUMMARY_LENGTH = 200  # Cap summary length

# Set CSV field size limit to prevent memory issues with malformed feeds
csv.field_size_limit(1_000_000)  # 1MB max


def minify_css(css: str) -> str:
    """Minify CSS by removing comments, whitespace, and newlines."""
    # Remove comments
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    # Remove whitespace around special characters
    css = re.sub(r'\s*([{};:,>])\s*', r'\1', css)
    # Collapse multiple whitespace
    css = re.sub(r'\s+', ' ', css)
    return css.strip()


def resolve_css_variables(css: str) -> str:
    """Replace CSS variables with their values (light mode only for email)."""
    # Extract variables from :root (first occurrence = light mode)
    root_match = re.search(r':root\s*\{([^}]+)\}', css)
    if not root_match:
        return css

    # Parse variables
    variables = {}
    for match in re.finditer(r'--([a-z-]+)\s*:\s*([^;]+);', root_match.group(1)):
        variables[match.group(1)] = match.group(2).strip()

    # Replace var(--name) with values
    def replace_var(match):
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    css = re.sub(r'var\(--([a-z-]+)\)', replace_var, css)

    # Remove :root blocks and @media (prefers-color-scheme) - not supported in email
    css = re.sub(r':root\s*\{[^}]+\}', '', css)
    css = re.sub(r'@media\s*\([^)]*prefers-color-scheme[^)]*\)\s*\{[^}]*\{[^}]*\}[^}]*\}', '', css)

    return css


def inline_styles(html: str) -> str:
    """Inline CSS styles for email compatibility using premailer."""
    try:
        from premailer import transform
        return transform(
            html,
            remove_classes=False,
            keep_style_tags=True,  # Keep for clients that support <style>
            strip_important=False,
            cssutils_logging_level=50,  # Suppress warnings
        )
    except ImportError:
        log("WARNING: premailer not installed, skipping CSS inlining")
        return html
    except Exception as e:
        log(f"WARNING: CSS inlining failed: {e}")
        return html


def replace_placeholders(digest_path: Path):
    """Replace all placeholders in digest HTML (styles, name, date, timestamp)."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B ") + str(now.day) + now.strftime(", %Y")
    date_url = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%A, ") + date_str + now.strftime(" · %H:%M UTC")
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    digest_domain = os.environ.get("DIGEST_DOMAIN", "")
    source_url = os.environ.get("SOURCE_URL", "")

    # Load CSS: resolve variables (for email) then minify
    if not STYLES_FILE.exists():
        raise RuntimeError(f"Styles file not found: {STYLES_FILE}")
    css = STYLES_FILE.read_text()
    css = resolve_css_variables(css)  # Replace var(--x) with actual values
    styles = minify_css(css)

    content = digest_path.read_text()

    # Verify required placeholders exist before replacing
    for placeholder in ["{{DIGEST_NAME}}", "{{DATE}}", "{{TIMESTAMP}}", "{{STYLES}}"]:
        if placeholder not in content:
            raise RuntimeError(f"Missing placeholder {placeholder} in digest")

    content = content.replace("{{STYLES}}", styles)
    content = content.replace("{{DIGEST_NAME}}", digest_name)
    content = content.replace("{{DATE}}", date_str)
    content = content.replace("{{TIMESTAMP}}", timestamp)

    # Optional: replace SOURCE_URL if configured, otherwise remove the link
    if source_url:
        content = content.replace("{{SOURCE_URL}}", source_url)
    else:
        # Remove the entire source link if not configured
        content = re.sub(r'\s*<a href="\{\{SOURCE_URL\}\}">[^<]+</a>\s*\([^)]+\)', '', content)

    # Add "View in browser" link if domain is configured
    if digest_domain:
        view_link = f'<p class="view-in-browser"><a href="https://{digest_domain}/{date_url}">View in browser</a></p>'
        content = content.replace("<body>", f"<body>\n  {view_link}")

    # Inline CSS for Gmail compatibility (premailer)
    content = inline_styles(content)

    digest_path.write_text(content)
    log(f"Timestamp: {timestamp}")


def prepare_claude_input(sources: list[dict]) -> list[Path]:
    """Prepare CSV input files for Claude - split if too large."""
    # Clean and recreate input directory
    if CLAUDE_INPUT_DIR.exists():
        shutil.rmtree(CLAUDE_INPUT_DIR)
    CLAUDE_INPUT_DIR.mkdir(parents=True)

    # Get previous headlines for deduplication
    previous_headlines = get_previous_headlines(days=7)

    # Write previous headlines CSV (for deduplication - Claude should not repeat these)
    previously_shown_file = CLAUDE_INPUT_DIR / "previously_shown.csv"
    with open(previously_shown_file, "w", newline="") as f:
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
                url = a.get("url", "")[:2000]  # Cap URL length
                # Skip articles with unsafe URL schemes (e.g., javascript:, data:)
                if not is_safe_url(url):
                    continue
                # Strip HTML, escape for safety, and cap lengths
                title = html.escape(strip_html(a.get("title") or ""))[:MAX_TITLE_LENGTH]
                summary = html.escape(strip_html(a.get("summary") or ""))[:MAX_SUMMARY_LENGTH]
                all_articles.append([
                    source["id"],
                    title,
                    url,
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

    log(f"Prepared CSV input: {len(all_articles)} new articles, {len(previous_headlines)} prior (dedup) in {len(article_files)} file(s)")
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
        return headlines
    except (json.JSONDecodeError, IOError) as e:
        log(f"Error reading shown_headlines.json: {e}")
        return []


def cleanup_shown_headlines():
    """Remove shown_headlines.json after successful run."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if headlines_file.exists():
        headlines_file.unlink()


def run_claude_command(command: str, description: str):
    """Run a Claude command with streaming output."""
    log(f"{description}...")
    process = subprocess.Popen(
        ["claude", "--print", "--permission-mode", "acceptEdits", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1  # Line buffered
    )
    try:
        # Stream output in real-time
        for line in process.stdout:
            print(line, end="", flush=True)
        process.wait()
    finally:
        # Ensure process is cleaned up even on interrupt
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
    if process.returncode != 0:
        raise RuntimeError(f"Claude failed with code {process.returncode}")


def generate_selections():
    """Pass 1: Run Claude to select and curate stories."""
    run_claude_command("/news-digest-select", "Pass 1: Selecting stories")


def validate_selections() -> dict:
    """Validate selections.json output from Pass 1."""
    selections_file = CLAUDE_INPUT_DIR / "selections.json"
    if not selections_file.exists():
        raise RuntimeError("selections.json not found - Pass 1 failed to create output")

    try:
        with open(selections_file) as f:
            selections = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"selections.json is invalid JSON: {e}")

    # Validate structure
    required_keys = ["must_know", "should_know", "quick_signals", "below_fold", "regional_summary", "stats"]
    missing = [k for k in required_keys if k not in selections]
    if missing:
        raise RuntimeError(f"selections.json missing required keys: {missing}")

    # Validate minimum counts
    must_know_count = len(selections.get("must_know", []))
    should_know_count = len(selections.get("should_know", []))
    quick_signals_count = len(selections.get("quick_signals", []))

    if must_know_count < 3:
        log(f"WARNING: Only {must_know_count} must_know stories (expected 3+)")
    if should_know_count < 5:
        log(f"WARNING: Only {should_know_count} should_know stories (expected 5+)")
    if quick_signals_count < 10:
        log(f"WARNING: Only {quick_signals_count} quick_signals (expected 10+)")

    # Log stats
    stats = selections.get("stats", {})
    log(f"Pass 1 complete: {stats.get('articles_reviewed', '?')} articles → {stats.get('stories_selected', '?')} stories")

    return selections


def generate_digest():
    """Pass 2: Run Claude to write HTML from selections."""
    run_claude_command("/news-digest-write", "Pass 2: Writing digest")


def validate_digest():
    """Validate HTML and shown_headlines.json output from Pass 2."""
    # Check HTML exists
    digest = find_latest_digest()
    if not digest:
        raise RuntimeError("No digest HTML found - Pass 2 failed to create output")

    # Basic HTML structure check
    html_content = digest.read_text()
    required_elements = ['<section>', 'class="why"', 'class="signal"', 'class="stats"']
    missing = [el for el in required_elements if el not in html_content]
    if missing:
        log(f"WARNING: HTML missing expected elements: {missing}")

    # Check shown_headlines.json
    headlines_file = DATA_DIR / "shown_headlines.json"
    if not headlines_file.exists():
        raise RuntimeError("shown_headlines.json not found - Pass 2 failed to create tracking file")

    try:
        with open(headlines_file) as f:
            headlines = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"shown_headlines.json is invalid JSON: {e}")

    if not isinstance(headlines, list):
        raise RuntimeError(f"shown_headlines.json should be array, got {type(headlines).__name__}")

    if headlines and not isinstance(headlines[0], dict):
        raise RuntimeError(f"shown_headlines.json items should be objects, got {type(headlines[0]).__name__}")

    if headlines and "headline" not in headlines[0]:
        raise RuntimeError(f"shown_headlines.json items missing 'headline' key")

    log(f"Pass 2 complete: {digest.name} ({len(headlines)} stories)")


def find_latest_digest() -> Path | None:
    """Find most recent digest file (HTML or TXT)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None


# =============================================================================
# Email
# =============================================================================

def send_health_alert(failing_sources: list[tuple[str, int]], failed_this_run: int, total_sources: int):
    """Send alert email when sources are persistently failing."""
    to_email = os.environ.get("HEALTH_ALERT_EMAIL")
    if not to_email:
        log("Skipping health alert: HEALTH_ALERT_EMAIL not set")
        return
    if not os.environ.get("RESEND_API_KEY"):
        log("Skipping health alert: RESEND_API_KEY not set")
        return

    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]

    source_list = "\n".join(f"  • {sid}: {count} consecutive failures" for sid, count in failing_sources)
    content = f"""<h2>News Digest Source Health Alert</h2>
<p><strong>{failed_this_run}/{total_sources}</strong> sources failed this run.</p>
<p>The following sources have failed 3+ times in a row:</p>
<pre>{source_list}</pre>
<p>Consider checking these feeds or removing them from sources.json.</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
"""

    try:
        resend.Emails.send({
            "from": f"News Digest Alerts <{from_email}>",
            "to": [to_email],
            "subject": f"[Alert] {len(failing_sources)} RSS sources failing",
            "html": content,
        })
        log(f"Health alert sent to {to_email}")
    except resend.exceptions.ResendError as e:
        log(f"Failed to send health alert: {e}")


def get_audience_contact_count(audience_id: str) -> int:
    """Get number of contacts in an audience."""
    try:
        contacts = resend.Contacts.list(audience_id=audience_id)
        if not isinstance(contacts, dict) or "data" not in contacts:
            log(f"WARNING: Unexpected response from Resend Contacts.list")
            return 0
        return len([c for c in contacts["data"] if not c.get("unsubscribed")])
    except resend.exceptions.ResendError as e:
        log(f"WARNING: Failed to get audience contact count: {e}")
        return 0


def send_broadcast(digest_path: Path) -> int:
    """Send digest via Resend Broadcasts API. Returns number of recipients."""
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    audience_id = os.environ["RESEND_AUDIENCE_ID"]

    content = digest_path.read_text()
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    try:
        # Get contact count before sending
        contact_count = get_audience_contact_count(audience_id)

        # Create the broadcast
        broadcast = resend.Broadcasts.create({
            "from": f"{digest_name} <{from_email}>",
            "audience_id": audience_id,
            "subject": f"{digest_name} – {date_str}",
            "html": content,
            "name": f"Digest {date_str}",
        })
        broadcast_id = broadcast["id"]
        log(f"Created broadcast: {broadcast_id}")

        # Send the broadcast immediately
        resend.Broadcasts.send({"broadcast_id": broadcast_id})
        log(f"Sent broadcast to {contact_count} contacts in audience {audience_id}")

        return contact_count
    except resend.exceptions.ResendError as e:
        log(f"Broadcast error: {e}")
        raise


# =============================================================================
# Main Pipeline
# =============================================================================

def validate_feeds(sources: list[dict]) -> int:
    """Test all RSS feeds and report health status. Returns exit code."""
    print(f"\n{'='*60}")
    print("RSS Feed Validation")
    print(f"{'='*60}")
    print(f"Testing {len(sources)} sources...\n")

    results = []
    total_articles = 0
    failed_count = 0

    for source in sources:
        source_id = source["id"]
        source_name = source["name"]
        url = source["url"]

        print(f"[{source_id}] {source_name}")
        print(f"  URL: {url[:80]}{'...' if len(url) > 80 else ''}")

        source_id, articles, error = fetch_source(source, timeout=15)

        if error:
            print(f"  Status: FAILED - {error}")
            failed_count += 1
            results.append((source_id, source_name, 0, error))
        else:
            article_count = len(articles)
            total_articles += article_count
            print(f"  Status: OK - {article_count} articles")

            # Parse dates and show range
            if articles:
                dates = [parse_date(a.get("published")) for a in articles]
                valid_dates = [d for d in dates if d is not None]
                if valid_dates:
                    oldest = min(valid_dates).strftime("%Y-%m-%d %H:%M")
                    newest = max(valid_dates).strftime("%Y-%m-%d %H:%M")
                    print(f"  Dates: {oldest} → {newest} ({len(valid_dates)}/{article_count} parseable)")
                else:
                    print(f"  Dates: No parseable dates found")

                # Show sample headline
                sample = articles[0].get("title", "")[:60]
                print(f"  Sample: \"{sample}{'...' if len(articles[0].get('title', '')) > 60 else ''}\"")
            results.append((source_id, source_name, article_count, None))
        print()

    # Summary
    print(f"{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    print(f"Total sources: {len(sources)}")
    print(f"Successful: {len(sources) - failed_count}")
    print(f"Failed: {failed_count}")
    print(f"Total articles: {total_articles}")

    if failed_count > 0:
        print(f"\nFailed sources:")
        for source_id, name, _, error in results:
            if error:
                print(f"  - {name} ({source_id}): {error}")

    # Check historical failures from DB
    init_db()
    persistently_failing = get_failing_sources(min_consecutive=3)
    if persistently_failing:
        print(f"\nSources with 3+ consecutive historical failures:")
        for sid, count in persistently_failing:
            print(f"  - {sid}: {count} failures")

    print()
    return 1 if failed_count > 0 else 0


def send_test_email(to_email: str) -> int:
    """Send a test email to verify Resend config."""
    for var in ["RESEND_API_KEY", "RESEND_FROM"]:
        if not os.environ.get(var):
            log(f"ERROR: Missing {var}")
            return 1
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")

    try:
        resend.Emails.send({
            "from": f"{digest_name} <{from_email}>",
            "to": [to_email],
            "subject": f"{digest_name} - Test Email",
            "html": "<p>This is a test email from News Digest.</p><p>If you received this, your Resend config is working.</p>",
        })
        log(f"Test email sent to {to_email}")
        return 0
    except resend.exceptions.ResendError as e:
        log(f"Resend error: {e}")
        return 1


def main():
    """Run full digest pipeline."""
    parser = argparse.ArgumentParser(
        description="News Digest Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                    # Full run: fetch, select, write, email, record
  python run.py --dry-run          # Generate but don't email or record
  python run.py --no-email         # Generate and record, but don't email
  python run.py --no-record        # Generate and email, but don't record
  python run.py --select-only      # Run Pass 1 only (create selections.json)
  python run.py --write-only       # Run Pass 2 only (use existing selections.json)
  python run.py --send-only        # Send latest digest (retry after failure)
  python run.py --preview          # Open latest digest in browser
  python run.py --test-email you@example.com  # Test Resend config
  python run.py --validate         # Test all RSS feeds and report status
        """
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and generate only (no email, no DB record)")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip sending email (still records to DB)")
    parser.add_argument("--no-record", action="store_true",
                        help="Skip recording to DB (still sends email)")
    parser.add_argument("--select-only", action="store_true",
                        help="Run Pass 1 only (selection) - creates selections.json")
    parser.add_argument("--write-only", action="store_true",
                        help="Run Pass 2 only (writing) - uses existing selections.json")
    parser.add_argument("--send-only", action="store_true",
                        help="Send latest digest without fetching/generating (for retrying after failure)")
    parser.add_argument("--preview", action="store_true",
                        help="Open latest digest in browser (no-op in Docker)")
    parser.add_argument("--test-email", metavar="EMAIL",
                        help="Send test email to specified address and exit")
    parser.add_argument("--validate", action="store_true",
                        help="Test all RSS feeds and report health status")
    args = parser.parse_args()

    # --dry-run is shorthand for --no-email --no-record
    skip_email = args.dry_run or args.no_email
    skip_record = args.dry_run or args.no_record

    # Test email mode - verify Resend config works
    if args.test_email:
        validate_env(dry_run=True)  # Don't require RESEND_AUDIENCE_ID for test
        return send_test_email(args.test_email)

    # Validate mode - test all RSS feeds
    if args.validate:
        sources = load_sources()
        return validate_feeds(sources)

    # Preview mode - open latest digest in browser
    if args.preview:
        digest = find_latest_digest()
        if not digest:
            log("ERROR: No digest found to preview")
            return 1
        if os.environ.get("IN_DOCKER"):
            log(f"Preview (Docker): {digest.absolute()}")
        else:
            log(f"Opening: {digest.name}")
            subprocess.run(["open", str(digest)])
        return 0

    # Send-only mode - retry broadcast from existing digest
    if args.send_only:
        validate_env(dry_run=False)
        init_db()
        digest = find_latest_digest()
        if not digest:
            log("ERROR: No digest found to send")
            return 1
        log(f"Sending existing digest: {digest.name}")
        save_digest(digest)  # Save before broadcast so link works
        recipients = send_broadcast(digest)
        shown_headlines = read_shown_headlines()
        if shown_headlines:
            record_shown_headlines(shown_headlines)
        record_run(0, articles_emailed=recipients)
        cleanup_shown_headlines()
        return 0

    # Write-only mode - run Pass 2 from existing selections
    if args.write_only:
        validate_env(dry_run=skip_email)
        init_db()
        validate_selections()  # Ensure selections.json exists and is valid
        generate_digest()
        validate_digest()
        digest = find_latest_digest()
        if not digest:
            log("ERROR: No digest generated")
            return 1
        replace_placeholders(digest)
        # Save before broadcast so link works
        if not skip_record:
            save_digest(digest)
        # Send broadcast
        recipients = 0
        if not skip_email:
            recipients = send_broadcast(digest)
        # Record run metadata
        if not skip_record:
            shown_headlines = read_shown_headlines()
            if shown_headlines:
                record_shown_headlines(shown_headlines)
            record_run(0, articles_emailed=recipients)
        cleanup_shown_headlines()
        return 0

    validate_env(dry_run=skip_email)  # Don't require SMTP vars if skipping email

    if not check_internet():
        log("No internet connection, skipping")
        return 0

    sources = load_sources()
    init_db()
    articles_fetched, failed_count = fetch_feeds(sources)

    # Send health alert if sources are persistently failing
    persistently_failing = get_failing_sources(min_consecutive=HEALTH_ALERT_THRESHOLD)
    if persistently_failing:
        send_health_alert(persistently_failing, failed_count, len(sources))

    # Prepare input for Claude (articles + previous headlines)
    prepare_claude_input(sources)

    # Pass 1: Select stories
    generate_selections()
    validate_selections()

    # Select-only mode - stop after Pass 1
    if args.select_only:
        log("Select-only mode: stopping after Pass 1")
        return 0

    # Pass 2: Write HTML digest
    generate_digest()
    validate_digest()

    digest = find_latest_digest()
    if not digest:
        log("ERROR: No digest generated")
        return 1
    replace_placeholders(digest)

    # Save digest to DB BEFORE broadcast so "view in browser" link works immediately
    if not skip_record:
        save_digest(digest)

    # Send broadcast
    recipients = 0
    if not skip_email:
        recipients = send_broadcast(digest)
    else:
        log(f"Skipping broadcast: {digest.name}")

    # Record run metadata after broadcast succeeds
    if not skip_record:
        shown_headlines = read_shown_headlines()
        if not shown_headlines:
            log("WARNING: No headlines recorded - Claude may not have generated shown_headlines.json")
        record_shown_headlines(shown_headlines)
        record_run(articles_fetched, articles_emailed=recipients)

    # Clean up shown_headlines.json only after successful completion
    cleanup_shown_headlines()

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
