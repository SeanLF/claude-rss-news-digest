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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
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
        if not re.match(r"^[a-z0-9_]+$", source["id"]):
            raise ValueError(
                f"sources.json[{i}] invalid id '{source['id']}': must be lowercase alphanumeric/underscore only"
            )

    return sources


# =============================================================================
# Utilities
# =============================================================================


def log(message: str, level: str = "INFO"):
    """Log with UTC timestamp and level to stdout and file (with rotation).

    Levels: INFO (default), WARN, ERROR
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] [{level}] {message}"
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
        req = urllib.request.Request("https://www.google.com/generate_204", headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=5)  # nosec B310
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"Internet check failed: {e}", "WARN")
        return False


def validate_env(dry_run: bool = False):
    """Check required environment variables. Exit if missing."""
    # ANTHROPIC_API_KEY is optional - Claude CLI can use `claude login` for Pro subscription
    required = []
    if not dry_run:
        required.extend(["RESEND_API_KEY", "RESEND_FROM", "RESEND_AUDIENCE_ID"])

    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        log(f"Missing environment variables: {', '.join(missing)}", "ERROR")
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
                log(f"Migration failed: {e}", "ERROR")
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
                return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=UTC)
    except sqlite3.Error as e:
        log(f"DB error getting last run time: {e}", "ERROR")
    return None


def record_run(articles_fetched: int, articles_emailed: int = 0):
    """Record a successful digest run."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO digest_runs (articles_fetched, articles_emailed) VALUES (?, ?)",
                (articles_fetched, articles_emailed),
            )
        log(f"Recorded run: {articles_fetched} fetched, {articles_emailed} emailed")
    except sqlite3.Error as e:
        log(f"DB error recording run: {e}", "ERROR")


def save_digest(digest_path: Path):
    """Save digest HTML to database for web serving."""
    # Extract date from filename (digest-YYYY-MM-DD*.html -> YYYY-MM-DD)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", digest_path.stem)
    if match:
        date_str = match.group(1)
    else:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        log(f"Could not extract date from '{digest_path.stem}', using {date_str}", "WARN")

    html_content = digest_path.read_text()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO digests (date, html) VALUES (?, ?)", (date_str, html_content))
        log(f"Saved digest to database: {date_str}")
    except sqlite3.Error as e:
        log(f"DB error saving digest: {e}", "ERROR")


def get_previous_headlines(days: int = 7) -> list[dict]:
    """Get headlines shown in the last N days for deduplication."""
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                SELECT headline, tier, date(shown_at) as date
                FROM shown_narratives
                WHERE shown_at > datetime('now', ?)
                ORDER BY shown_at DESC
            """,
                (f"-{days} days",),
            )
            return [{"headline": row[0], "tier": row[1], "date": row[2]} for row in cursor.fetchall()]
    except sqlite3.Error as e:
        log(f"DB error getting previous headlines: {e}", "ERROR")
        return []


def record_shown_headlines(headlines: list[dict]):
    """Record headlines that were shown in this digest."""
    if not headlines:
        return
    # Validate format before processing
    if headlines and not isinstance(headlines[0], dict):
        log(
            f"shown_headlines.json has wrong format - expected list of dicts, got list of {type(headlines[0]).__name__}",
            "ERROR",
        )
        log(f"First item: {repr(headlines[0][:100]) if isinstance(headlines[0], str) else repr(headlines[0])}", "ERROR")
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier) VALUES (?, ?)",
                [(h.get("headline", ""), h.get("tier", "")) for h in headlines],
            )
        log(f"Saved {len(headlines)} headlines to dedup history")
    except sqlite3.Error as e:
        log(f"DB error recording headlines: {e}", "ERROR")


def record_source_health(results: list[tuple[str, bool, str | None]]):
    """Record source fetch results. Each tuple is (source_id, success, error_message)."""
    if not results:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("INSERT INTO source_health (source_id, success, error_message) VALUES (?, ?, ?)", results)
    except sqlite3.Error as e:
        log(f"DB error recording source health for {len(results)} sources: {e}", "ERROR")


def get_consecutive_failures(source_id: str, limit: int = 10) -> int:
    """Get count of consecutive recent failures for a source."""
    if not DB_PATH.exists():
        return 0
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                SELECT success FROM source_health
                WHERE source_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
            """,
                (source_id, limit),
            )
            count = 0
            for (success,) in cursor:
                if success:
                    break
                count += 1
            return count
    except sqlite3.Error as e:
        log(f"DB error getting consecutive failures for {source_id}: {e}", "ERROR")
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
        log(f"DB error getting failing sources: {e}", "ERROR")
        return []

    failing = [(sid, count) for sid in source_ids if (count := get_consecutive_failures(sid)) >= min_consecutive]
    return sorted(failing, key=lambda x: -x[1])


# =============================================================================
# RSS Fetching
# =============================================================================


def parse_date(date_str: str | None) -> datetime | None:
    """Parse RSS date formats (ISO 8601 or RFC 2822)."""
    if not date_str:
        return None
    try:
        # ISO 8601: 2025-01-15T10:30:00Z (has digit-T-digit pattern)
        if re.search(r"\dT\d", date_str):
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(UTC)
        # RFC 2822: Tue, 15 Jan 2025 10:30:00 GMT
        return parsedate_to_datetime(date_str).astimezone(UTC)
    except (ValueError, TypeError):
        # Don't log - too noisy for date parsing
        return None


def fetch_source(source: dict, timeout: int = 15) -> tuple[str, list[dict], str | None]:
    """Fetch single RSS source with retry logic. Returns (source_id, articles, error_or_none)."""
    source_id = source["id"]
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
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
                        pub_str = datetime(*published[:6], tzinfo=UTC).isoformat()  # type: ignore[misc]
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

            return source_id, articles, None  # Success

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Transient errors - retry with exponential backoff
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2**attempt)  # 1s, 2s, 4s
                time.sleep(delay)
            continue
        except Exception as e:
            # Non-transient error - don't retry
            error_msg = f"{type(e).__name__}: {e}"
            print(f"  [{source_id}] Error: {error_msg}", flush=True)
            return source_id, [], error_msg

    # All retries exhausted
    error_msg = str(getattr(last_error, "reason", last_error)) if last_error else "Unknown"
    print(f"  [{source_id}] Failed after {MAX_RETRIES} retries: {error_msg}", flush=True)
    return source_id, [], f"Failed after {MAX_RETRIES} retries: {error_msg}"


def fetch_feeds(sources: list[dict]) -> tuple[int, int]:
    """Fetch all RSS feeds in parallel. Returns (total_articles, failed_count)."""
    log(f"Fetching {len(sources)} RSS feeds...")

    last_run = get_last_run_time()

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

    # Filter by date and save, tracking per-source counts
    total_kept = 0
    total_fetched = 0
    per_source_counts = []  # (source_id, fetched, kept)
    for source in sources:
        source_id = source["id"]
        articles = results.get(source_id, [])
        fetched_count = len(articles)
        if last_run:
            articles = [a for a in articles if (pub := parse_date(a.get("published"))) is None or pub > last_run]
        kept_count = len(articles)

        with open(FETCHED_DIR / f"{source_id}.json", "w") as out_file:
            json.dump(articles, out_file, indent=2)
        total_kept += kept_count
        total_fetched += fetched_count
        per_source_counts.append((source_id, fetched_count, kept_count))

    # Per-source breakdown (show sources with articles, sorted by kept desc)
    sources_with_articles = [(sid, f, k) for sid, f, k in per_source_counts if f > 0]
    if sources_with_articles:
        if last_run:
            print(f"  Filtering after: {last_run.isoformat()} (kept/fetched)", flush=True)
        sources_with_articles.sort(key=lambda x: (-x[2], -x[1]))  # Sort by kept desc, then fetched desc
        for sid, fetched, kept in sources_with_articles:
            print(f"  [{sid}] {kept}/{fetched}", flush=True)

    # Summary
    failed_this_run = [(sid, err) for sid, success, err in health_records if not success]
    succeeded = len(sources) - len(failed_this_run)
    log(f"Fetched {total_kept}/{total_fetched} articles from {succeeded}/{len(sources)} sources")

    if failed_this_run:
        log(f"Failed sources this run: {', '.join(sid for sid, _ in failed_this_run)}", "WARN")

    # Check for persistently failing sources
    persistently_failing = get_failing_sources(min_consecutive=HEALTH_ALERT_THRESHOLD)
    if persistently_failing:
        log(
            f"Sources with {HEALTH_ALERT_THRESHOLD}+ consecutive failures: {', '.join(f'{sid}({n}x)' for sid, n in persistently_failing)}",
            "WARN",
        )

    return total_kept, len(failed_this_run)


# =============================================================================
# Digest Generation
# =============================================================================


def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars/token for CSV with URLs)."""
    return len(text) // 4


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)  # Remove tags
    text = html.unescape(text)  # Decode &amp; etc
    text = re.sub(r"\s+", " ", text).strip()  # Normalize whitespace
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
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Remove whitespace around special characters
    css = re.sub(r"\s*([{};:,>])\s*", r"\1", css)
    # Collapse multiple whitespace
    css = re.sub(r"\s+", " ", css)
    return css.strip()


def resolve_css_variables(css: str) -> str:
    """Replace CSS variables with their values (light mode only for email).

    Email clients don't support CSS variables or prefers-color-scheme, so we
    resolve to light mode values and strip the dark mode media query.
    """
    # Extract variables from :root (first occurrence = light mode)
    root_match = re.search(r":root\s*\{([^}]+)\}", css)
    if not root_match:
        return css

    # Parse variables
    variables = {}
    for match in re.finditer(r"--([a-z-]+)\s*:\s*([^;]+);", root_match.group(1)):
        variables[match.group(1)] = match.group(2).strip()

    # Replace var(--name) with values
    def replace_var(match):
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    css = re.sub(r"var\(--([a-z-]+)\)", replace_var, css)

    # Remove :root blocks and @media (prefers-color-scheme) - not supported in email
    css = re.sub(r":root\s*\{[^}]+\}", "", css)
    css = re.sub(r"@media\s*\([^)]*prefers-color-scheme[^)]*\)\s*\{[^}]*\{[^}]*\}[^}]*\}", "", css)

    return css


def prepare_for_email(html_content: str) -> str:
    """Prepare HTML for email delivery.

    Resolves CSS variables to light mode values and inlines styles.
    Email clients don't support CSS variables or prefers-color-scheme.
    """

    # Extract and transform the <style> content
    def resolve_style_block(match):
        css = match.group(1)
        resolved_css = resolve_css_variables(css)
        minified_css = minify_css(resolved_css)
        return f"<style>{minified_css}</style>"

    html_content = re.sub(r"<style>([^<]+)</style>", resolve_style_block, html_content)

    # Inline styles for Gmail compatibility
    html_content = inline_styles(html_content)

    return html_content


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
        log("premailer not installed, skipping CSS inlining", "WARN")
        return html
    except Exception as e:
        log(f"CSS inlining failed: {e}", "WARN")
        return html


TEMPLATE_FILE = APP_DIR / "digest-template.html"

# Region display configuration: (display_name, emoji)
REGION_CONFIG = {
    "europe": ("Europe", "🌍"),
    "americas": ("Americas", "🌎"),
    "asia_pacific": ("Asia-Pacific", "🌏"),
    "middle_east_africa": ("Middle East & Africa", "🌍"),
    "tech": ("Tech", "🤖"),
}

# Region display order (Americas first - where subscribers are)
REGION_ORDER = ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"]


def markdown_to_html(text: str) -> str:
    """Convert markdown links [text](url) to HTML <a> tags."""

    def replace_link(match):
        link_text = html.escape(match.group(1))
        url = match.group(2)
        if is_safe_url(url):
            return f'<a href="{html.escape(url)}">{link_text}</a>'
        return link_text  # Return just text if URL is unsafe

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)


def render_article(article: dict, include_reporting_varies: bool = True) -> str:
    """Render a single article (must_know or should_know) to HTML."""
    headline = html.escape(article.get("headline", ""))
    summary = html.escape(article.get("summary", ""))
    why = html.escape(article.get("why_it_matters", ""))

    # Sources line
    sources_html = []
    for src in article.get("sources", []):
        name = html.escape(src.get("name", ""))
        url = src.get("url", "")
        bias = html.escape(src.get("bias", ""))
        if name and url and is_safe_url(url):
            sources_html.append(f'<a href="{html.escape(url)}">{name}</a> ({bias})')
    sources_line = " · ".join(sources_html)

    # Build article HTML
    parts = [
        "    <article>",
        f"      <h3>{headline}</h3>",
        f"      <p>{summary}</p>",
        f'      <p class="why"><strong>Why it matters:</strong> {why}</p>',
    ]

    # Optional: reporting_varies (only for must_know)
    if include_reporting_varies:
        reporting_varies = article.get("reporting_varies", [])
        if reporting_varies:
            parts.append('      <div class="reporting-varies">')
            parts.append("        <strong>How reporting varies:</strong>")
            parts.append("        <ul>")
            for rv in reporting_varies:
                src = html.escape(rv.get("source", ""))
                bias = html.escape(rv.get("bias", ""))
                angle = html.escape(rv.get("angle", ""))
                parts.append(f"          <li><em>{src}</em> ({bias}): {angle}</li>")
            parts.append("        </ul>")
            parts.append("      </div>")

    parts.append(f'      <p class="sources">{sources_line}</p>')
    parts.append("    </article>")

    return "\n".join(parts)


def render_signal(item: dict) -> str:
    """Render a quick signal or below_fold item to HTML."""
    headline = html.escape(item.get("headline", ""))
    src = item.get("source", {})
    name = html.escape(src.get("name", ""))
    url = src.get("url", "")
    if url and is_safe_url(url):
        return f'      <p class="signal">{headline} — <a href="{html.escape(url)}">{name}</a></p>'
    return f'      <p class="signal">{headline} — {name}</p>'


def render_digest(selections: dict) -> str:
    """Render selections.json to complete HTML string."""
    # Load template
    if not TEMPLATE_FILE.exists():
        raise RuntimeError(f"Template file not found: {TEMPLATE_FILE}")
    template = TEMPLATE_FILE.read_text()

    # Render regional summary
    regional_summary = selections.get("regional_summary", {})
    summary_parts = []
    for region_key in REGION_ORDER:
        text = regional_summary.get(region_key, "")
        if text:
            region_name, emoji = REGION_CONFIG[region_key]
            text_html = markdown_to_html(text)
            summary_parts.append(f'    <p><span class="region">{emoji} {region_name}:</span> {text_html}</p>')
    summary_html = "\n".join(summary_parts)

    # Render must_know
    must_know_html = "\n".join(
        render_article(article, include_reporting_varies=True) for article in selections.get("must_know", [])
    )

    # Render should_know
    should_know_html = "\n".join(
        render_article(article, include_reporting_varies=False) for article in selections.get("should_know", [])
    )

    # Render signals (clustered by region)
    signals = selections.get("signals", {})
    cluster_parts = []
    for region_key in REGION_ORDER:
        items = signals.get(region_key, [])
        if items:
            region_name, emoji = REGION_CONFIG[region_key]
            cluster_parts.append('    <div class="cluster">')
            cluster_parts.append(f"      <h3>{emoji} {region_name}</h3>")
            for item in items:
                cluster_parts.append(render_signal(item))
            cluster_parts.append("    </div>")
    signals_html = "\n".join(cluster_parts)

    # Fill template
    result = template
    result = result.replace("{{REGIONAL_SUMMARY}}", summary_html)
    result = result.replace("{{MUST_KNOW}}", must_know_html)
    result = result.replace("{{SHOULD_KNOW}}", should_know_html)
    result = result.replace("{{SIGNALS}}", signals_html)

    return result


def extract_headlines(selections: dict) -> list[dict]:
    """Extract all headlines from selections for deduplication tracking."""
    headlines = []

    # Top-tier articles (must_know, should_know)
    for tier in ["must_know", "should_know"]:
        for item in selections.get(tier, []):
            headlines.append({"headline": item.get("headline", ""), "tier": tier})

    # Signals by cluster
    signals = selections.get("signals", {})
    for cluster in REGION_ORDER:
        for item in signals.get(cluster, []):
            headlines.append({"headline": item.get("headline", ""), "tier": "signal", "cluster": cluster})

    return headlines


def extract_preheader(selections: dict, max_length: int = 150) -> str:
    """Extract preheader text from first regional summary for email preview."""
    regional_summary = selections.get("regional_summary", {})
    for region in REGION_ORDER:
        summary = regional_summary.get(region, "")
        if summary:
            # Strip markdown links, get plain text
            plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
            # Get first sentence or truncate
            first_sentence = plain.split(".")[0] + "."
            if len(first_sentence) <= max_length:
                return first_sentence
            return plain[:max_length].rsplit(" ", 1)[0] + "..."
    return ""


def replace_placeholders(digest_path: Path, preheader: str = ""):
    """Replace all placeholders in digest HTML (styles, name, date, timestamp).

    CSS variables are preserved to support dark mode when viewing in browser.
    Email preparation (resolving variables, inlining) happens in send_broadcast().
    """
    now = datetime.now(UTC)
    date_str = now.strftime("%B ") + str(now.day) + now.strftime(", %Y")
    date_url = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%A, ") + date_str + now.strftime(" · %H:%M UTC")
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    digest_domain = os.environ.get("DIGEST_DOMAIN", "")
    source_url = os.environ.get("SOURCE_URL", "")
    model_name = os.environ.get("MODEL_NAME", "Claude")
    archive_url = os.environ.get("ARCHIVE_URL", "")

    # Load CSS: minify but keep variables for dark mode support in browser
    if not STYLES_FILE.exists():
        raise RuntimeError(f"Styles file not found: {STYLES_FILE}")
    css = STYLES_FILE.read_text()
    styles = minify_css(css)  # Keep CSS variables intact

    content = digest_path.read_text()

    # Verify required placeholders exist before replacing
    for placeholder in ["{{DIGEST_NAME}}", "{{DATE}}", "{{TIMESTAMP}}", "{{STYLES}}"]:
        if placeholder not in content:
            raise RuntimeError(f"Missing placeholder {placeholder} in digest")

    content = content.replace("{{STYLES}}", styles)
    content = content.replace("{{DIGEST_NAME}}", html.escape(digest_name))
    content = content.replace("{{DATE}}", date_str)
    content = content.replace("{{TIMESTAMP}}", timestamp)
    content = content.replace("{{MODEL_NAME}}", html.escape(model_name))
    content = content.replace("{{PREHEADER}}", html.escape(preheader))

    # Optional: replace SOURCE_URL if configured, otherwise remove the links
    if source_url:
        content = content.replace("{{SOURCE_URL}}", source_url)
    else:
        # Remove the open source sentence in AI notice if not configured
        content = re.sub(
            r'\s*This project is <a href="\{\{SOURCE_URL\}\}">[^<]+</a> and contributions are welcome\.', "", content
        )
        # Remove the feedback paragraph that uses SOURCE_URL for issues link
        content = re.sub(r'\s*<p>Feedback\?[^<]*<a href="\{\{SOURCE_URL\}\}/issues">[^<]+</a></p>', "", content)

    # Optional: author plug (e.g., "Made by Sean · seanfloyd.dev")
    author_name = os.environ.get("AUTHOR_NAME", "")
    author_url = os.environ.get("AUTHOR_URL", "")
    if author_name and author_url and is_safe_url(author_url):
        author_plug = f'Made by <a href="{html.escape(author_url)}">{html.escape(author_name)}</a>'
        content = content.replace("{{AUTHOR_PLUG}}", author_plug)
    elif author_name:
        content = content.replace("{{AUTHOR_PLUG}}", f"Made by {html.escape(author_name)}")
    else:
        # Remove the author plug paragraph if not configured
        content = re.sub(r"\s*<p>\{\{AUTHOR_PLUG\}\}</p>", "", content)

    # Replace HOMEPAGE_URL for "View in browser" link
    if digest_domain:
        homepage_url = f"https://{digest_domain}/{date_url}"
        content = content.replace("{{HOMEPAGE_URL}}", homepage_url)
    else:
        # Remove the view-in-browser paragraph if not configured
        content = re.sub(
            r'\s*<p class="view-in-browser">[^<]*<a href="\{\{HOMEPAGE_URL\}\}">[^<]+</a></p>', "", content
        )

    # Optional: archive URL for "Past digests" link
    if archive_url and is_safe_url(archive_url):
        content = content.replace("{{ARCHIVE_URL}}", html.escape(archive_url))
    else:
        # Remove the archive link, keep just unsubscribe
        content = re.sub(r'<a href="\{\{ARCHIVE_URL\}\}">[^<]+</a> · ', "", content)

    # Note: CSS variable resolution and style inlining happen in send_broadcast()
    # to preserve dark mode support for web viewing

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
                all_articles.append([source["id"], title, url, a.get("published", ""), summary])

    # Split articles into multiple files if needed
    article_files = []
    current_file_num = 1
    current_rows: list[list[str]] = []
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

    log(
        f"Prepared CSV input: {len(all_articles)} new articles, {len(previous_headlines)} prior (dedup) in {len(article_files)} file(s)"
    )
    return article_files


def read_shown_headlines() -> list[dict]:
    """Read shown_headlines.json output from Claude."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if not headlines_file.exists():
        log("shown_headlines.json not found", "WARN")
        return []

    try:
        with open(headlines_file) as f:
            headlines = json.load(f)
        return headlines
    except (OSError, json.JSONDecodeError) as e:
        log(f"Error reading shown_headlines.json: {e}", "ERROR")
        return []


def cleanup_shown_headlines():
    """Remove shown_headlines.json after successful run."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if headlines_file.exists():
        headlines_file.unlink()


def run_claude_command(command: str, description: str, mcp_config: str | None = None):
    """Run a Claude command with streaming output."""
    log(f"{description}...")
    cmd = ["claude", "--print", "--permission-mode", "acceptEdits", command]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config, "--allowedTools", "mcp__news-digest__write_selections"])
    log(f"Running: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # Line buffered
    )
    try:
        # Stream output in real-time
        assert process.stdout is not None, "stdout=PIPE guarantees this"  # nosec B101
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
    run_claude_command("/news-digest-select", "Pass 1: Selecting stories", mcp_config=".mcp.json")


def validate_source(src: dict, context: str) -> list[str]:
    """Validate a source object. Returns list of errors."""
    errors = []
    if not src.get("name"):
        errors.append(f"{context}: source missing 'name'")
    if not src.get("url"):
        errors.append(f"{context}: source missing 'url'")
    elif not is_safe_url(src.get("url", "")):
        errors.append(f"{context}: source has unsafe URL scheme")
    return errors


def validate_article(article: dict, tier: str, idx: int) -> list[str]:
    """Validate a must_know or should_know article. Returns list of errors."""
    errors = []
    context = f"{tier}[{idx}]"

    if not article.get("headline"):
        errors.append(f"{context}: missing 'headline'")
    if not article.get("summary"):
        errors.append(f"{context}: missing 'summary'")
    # why_it_matters is optional - schema fixer sets empty string if missing
    if not article.get("why_it_matters"):
        log(f"{context}: missing 'why_it_matters'", "WARN")

    sources = article.get("sources", [])
    if not sources:
        errors.append(f"{context}: missing 'sources'")
    for j, src in enumerate(sources):
        errors.extend(validate_source(src, f"{context}.sources[{j}]"))

    return errors


def validate_signal(item: dict, tier: str, idx: int, cluster: str | None = None) -> list[str]:
    """Validate a quick_signal or below_fold item. Returns list of errors."""
    context = f"{tier}.{cluster}[{idx}]" if cluster else f"{tier}[{idx}]"
    errors = []

    if not isinstance(item, dict):
        return errors  # Skip non-dict items (shouldn't happen after schema fix)

    if not item.get("headline"):
        errors.append(f"{context}: missing 'headline'")

    src = item.get("source")
    if not src:
        errors.append(f"{context}: missing 'source'")
    elif isinstance(src, dict):
        # For signals, only warn on missing URL (may be converted from string)
        if not src.get("name"):
            errors.append(f"{context}: source missing 'name'")
        if not src.get("url"):
            log(f"{context}: signal source missing 'url'", "WARN")

    return errors


def fix_selections_schema(selections: dict) -> dict:
    """Fix common schema deviations from Claude's output."""
    fixed = 0

    # Fix must_know and should_know articles
    for tier in ["must_know", "should_know"]:
        for article in selections.get(tier, []):
            # Fix: title instead of headline
            if "title" in article and "headline" not in article:
                article["headline"] = article.pop("title")
                fixed += 1

            # Fix: links array instead of url in sources
            if "links" in article and article.get("sources"):
                links = article.pop("links")
                for i, src in enumerate(article["sources"]):
                    if not src.get("url") and i < len(links):
                        src["url"] = links[i]
                        fixed += 1

            # Fix: missing why_it_matters - use empty string (will warn but not fail)
            if "why_it_matters" not in article:
                article["why_it_matters"] = ""
                fixed += 1

    # Fix signals - may have one_liner/link instead of headline/source, or be plain strings
    signals = selections.get("signals", {})
    for cluster_name, cluster in list(signals.items()):
        if not isinstance(cluster, list):
            continue
        fixed_cluster = []
        for item in cluster:
            if isinstance(item, str):
                # Convert plain string to object format
                fixed_cluster.append({"headline": item, "source": {"name": "Source", "url": "", "bias": "center"}})
                fixed += 1
            elif isinstance(item, dict):
                if "one_liner" in item and "headline" not in item:
                    item["headline"] = item.pop("one_liner")
                    fixed += 1
                if "link" in item and "source" not in item:
                    item["source"] = {"name": "Source", "url": item.pop("link"), "bias": "center"}
                    fixed += 1
                fixed_cluster.append(item)
        signals[cluster_name] = fixed_cluster

    # Fix regional_summary - may be a single string instead of per-region dict
    regional_summary = selections.get("regional_summary", {})
    if isinstance(regional_summary, str):
        # Split by ** headers or use as-is for all regions
        selections["regional_summary"] = {
            "americas": regional_summary,
            "europe": "",
            "asia_pacific": "",
            "middle_east_africa": "",
            "tech": "",
        }
        fixed += 1

    if fixed > 0:
        log(f"Fixed {fixed} schema deviations in selections.json")

    return selections


def validate_selections() -> dict:
    """Validate selections.json output from Pass 1."""
    selections_file = CLAUDE_INPUT_DIR / "selections.json"
    if not selections_file.exists():
        raise RuntimeError("selections.json not found - Pass 1 failed to create output")

    try:
        with open(selections_file) as f:
            selections = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"selections.json is invalid JSON: {e}") from e

    # Fix common schema deviations before validation
    selections = fix_selections_schema(selections)

    # Validate required top-level keys
    required_keys = ["must_know", "should_know", "signals", "regional_summary"]
    missing = [k for k in required_keys if k not in selections]
    if missing:
        raise RuntimeError(f"selections.json missing required keys: {missing}")

    errors = []

    # Validate must_know articles
    for i, article in enumerate(selections.get("must_know", [])):
        errors.extend(validate_article(article, "must_know", i))

    # Validate should_know articles
    for i, article in enumerate(selections.get("should_know", [])):
        errors.extend(validate_article(article, "should_know", i))

    # Validate signals (clustered by region)
    signals = selections.get("signals", {})
    for cluster in REGION_ORDER:
        for i, item in enumerate(signals.get(cluster, [])):
            errors.extend(validate_signal(item, "signals", i, cluster))

    # Validate regional_summary has content
    regional_summary = selections.get("regional_summary", {})
    for region in REGION_ORDER:
        if not regional_summary.get(region):
            errors.append(f"regional_summary.{region}: missing or empty")

    # Report validation errors
    if errors:
        for err in errors[:10]:  # Show first 10 errors
            log(f"Validation: {err}", "ERROR")
        if len(errors) > 10:
            log(f"... and {len(errors) - 10} more errors", "ERROR")
        raise RuntimeError(f"selections.json has {len(errors)} validation errors")

    # Validate minimum counts (warnings only)
    must_know_count = len(selections.get("must_know", []))
    should_know_count = len(selections.get("should_know", []))
    signals_count = sum(len(signals.get(cluster, [])) for cluster in REGION_ORDER)

    if must_know_count < 3:
        log(f"Only {must_know_count} must_know stories (expected 3+)", "WARN")
    if should_know_count < 5:
        log(f"Only {should_know_count} should_know stories (expected 5+)", "WARN")

    # Log summary
    total_stories = must_know_count + should_know_count + signals_count
    log(f"Pass 1 complete: {total_stories} stories selected")

    return selections


def write_digest_from_selections(selections: dict) -> Path:
    """Render selections to HTML and write digest file. Returns digest path."""
    # Log stats
    must_know = len(selections.get("must_know", []))
    should_know = len(selections.get("should_know", []))
    signals = selections.get("signals", {})
    signals_count = sum(len(signals.get(c, [])) for c in REGION_ORDER)
    log(f"Rendering: {must_know} must_know, {should_know} should_know, {signals_count} signals")

    # Render HTML
    html_content = render_digest(selections)

    # Generate filename with timestamp
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%MZ")
    digest_path = OUTPUT_DIR / f"digest-{timestamp}.html"

    # Write HTML file
    digest_path.write_text(html_content)

    # Extract and write headlines for deduplication
    headlines = extract_headlines(selections)
    headlines_file = DATA_DIR / "shown_headlines.json"
    with open(headlines_file, "w") as f:
        json.dump(headlines, f, indent=2)

    log(f"Wrote {digest_path.name} ({len(headlines)} stories)")
    return digest_path


def find_latest_digest() -> Path | None:
    """Find most recent digest file (HTML or TXT)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None


# =============================================================================
# Email
# =============================================================================


def resend_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call Resend API with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except resend.exceptions.ResendError as e:
            is_rate_limited = "Too many requests" in str(e)
            has_retries_left = attempt < max_retries - 1
            if is_rate_limited and has_retries_left:
                delay = 2**attempt
                log(f"Rate limited, retrying in {delay}s...", "WARN")
                time.sleep(delay)
            else:
                raise


def send_health_alert(failing_sources: list[tuple[str, int]], failed_this_run: int, total_sources: int):
    """Send alert email when sources are persistently failing."""
    to_email = os.environ.get("HEALTH_ALERT_EMAIL")
    if not to_email:
        log("Skipping health alert: HEALTH_ALERT_EMAIL not set", "WARN")
        return
    if not os.environ.get("RESEND_API_KEY"):
        log("Skipping health alert: RESEND_API_KEY not set", "WARN")
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
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"News Digest Alerts <{from_email}>",
                "to": [to_email],
                "subject": f"[Alert] {len(failing_sources)} RSS sources failing",
                "html": content,
            },
        )
        log(f"Health alert sent to {to_email}")
    except resend.exceptions.ResendError as e:
        log(f"Failed to send health alert: {e}", "ERROR")


def get_audience_contact_count(audience_id: str) -> int:
    """Get number of contacts in an audience."""
    try:
        contacts = resend_with_retry(resend.Contacts.list, audience_id=audience_id)
        if not isinstance(contacts, dict) or "data" not in contacts:
            log("Unexpected response from Resend Contacts.list", "WARN")
            return 0
        return len([c for c in contacts["data"] if not c.get("unsubscribed")])
    except resend.exceptions.ResendError as e:
        log(f"Failed to get audience contact count: {e}", "WARN")
        return 0


def send_broadcast(digest_path: Path) -> int:
    """Send digest via Resend Broadcasts API. Returns number of recipients."""
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    audience_id = os.environ["RESEND_AUDIENCE_ID"]

    content = digest_path.read_text()
    # Prepare for email: resolve CSS variables and inline styles
    content = prepare_for_email(content)
    date_str = datetime.now(UTC).strftime("%B %d, %Y")

    try:
        # Get contact count before sending
        contact_count = get_audience_contact_count(audience_id)

        # Create the broadcast
        broadcast = resend_with_retry(
            resend.Broadcasts.create,
            {
                "from": f"{digest_name} <{from_email}>",
                "audience_id": audience_id,
                "subject": f"{digest_name} – {date_str}",
                "html": content,
                "name": f"Digest {date_str}",
            },
        )
        broadcast_id = broadcast["id"]
        log(f"Created broadcast: {broadcast_id}")

        # Send the broadcast
        resend_with_retry(resend.Broadcasts.send, {"broadcast_id": broadcast_id})
        log(f"Sent broadcast to {contact_count} contacts in audience {audience_id}")

        return contact_count
    except resend.exceptions.ResendError as e:
        log(f"Broadcast error: {e}", "ERROR")
        raise


# =============================================================================
# Main Pipeline
# =============================================================================


def validate_single_feed(source: dict) -> dict:
    """Validate a single RSS feed. Returns result dict with status and metadata."""
    source_id = source["id"]
    _, articles, error = fetch_source(source, timeout=15)

    result = {
        "id": source_id,
        "name": source["name"],
        "url": source["url"],
        "status": "failed" if error else "ok",
        "article_count": 0,
        "error": error,
    }

    if error:
        return result

    result["article_count"] = len(articles)

    if articles:
        dates = [parse_date(a.get("published")) for a in articles]
        valid_dates = [d for d in dates if d is not None]
        result["parseable_dates"] = len(valid_dates)
        if valid_dates:
            result["oldest_article"] = min(valid_dates).isoformat()
            result["newest_article"] = max(valid_dates).isoformat()
        result["sample_headline"] = articles[0].get("title", "")

    return result


def print_feed_result(result: dict):
    """Print human-readable validation result for a single feed."""
    print(f"[{result['id']}] {result['name']}")
    url = result["url"]
    print(f"  URL: {url[:80]}{'...' if len(url) > 80 else ''}")

    if result["error"]:
        print(f"  Status: FAILED - {result['error']}")
    else:
        article_count = result["article_count"]
        print(f"  Status: OK - {article_count} articles")

        if result.get("oldest_article"):
            oldest = datetime.fromisoformat(result["oldest_article"])
            newest = datetime.fromisoformat(result["newest_article"])
            parseable = result.get("parseable_dates", 0)
            print(
                f"  Dates: {oldest.strftime('%Y-%m-%d %H:%M')} → {newest.strftime('%Y-%m-%d %H:%M')} ({parseable}/{article_count} parseable)"
            )
        elif article_count > 0:
            print("  Dates: No parseable dates found")

        if result.get("sample_headline"):
            sample = result["sample_headline"][:60]
            ellipsis = "..." if len(result["sample_headline"]) > 60 else ""
            print(f'  Sample: "{sample}{ellipsis}"')

    print()


def validate_feeds(sources: list[dict], json_output: bool = False) -> int:
    """Test all RSS feeds and report health status. Returns exit code."""
    if not json_output:
        print(f"\n{'=' * 60}")
        print("RSS Feed Validation")
        print(f"{'=' * 60}")
        print(f"Testing {len(sources)} sources...\n")

    # Collect results
    results = [validate_single_feed(source) for source in sources]
    if not json_output:
        for result in results:
            print_feed_result(result)

    # Compute summary stats
    failed_count = sum(1 for r in results if r["error"])
    total_articles = sum(r["article_count"] for r in results)

    # Check historical failures from DB
    init_db()
    persistently_failing = get_failing_sources(min_consecutive=3)

    if json_output:
        output = {
            "total_sources": len(sources),
            "successful": len(sources) - failed_count,
            "failed": failed_count,
            "total_articles": total_articles,
            "sources": results,
            "persistently_failing": [{"id": sid, "consecutive_failures": count} for sid, count in persistently_failing],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"{'=' * 60}")
        print("Summary")
        print(f"{'=' * 60}")
        print(f"Total sources: {len(sources)}")
        print(f"Successful: {len(sources) - failed_count}")
        print(f"Failed: {failed_count}")
        print(f"Total articles: {total_articles}")

        if failed_count > 0:
            print("\nFailed sources:")
            for r in results:
                if r["error"]:
                    print(f"  - {r['name']} ({r['id']}): {r['error']}")

        if persistently_failing:
            print("\nSources with 3+ consecutive historical failures:")
            for sid, count in persistently_failing:
                print(f"  - {sid}: {count} failures")

        print()

    return 1 if failed_count > 0 else 0


def send_test_email(to_email: str) -> int:
    """Send a test email to verify Resend config."""
    for var in ["RESEND_API_KEY", "RESEND_FROM"]:
        if not os.environ.get(var):
            log(f"Missing {var}", "ERROR")
            return 1
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")

    try:
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"{digest_name} <{from_email}>",
                "to": [to_email],
                "subject": f"{digest_name} - Test Email",
                "html": "<p>This is a test email from News Digest.</p><p>If you received this, your Resend config is working.</p>",
            },
        )
        log(f"Test email sent to {to_email}")
        return 0
    except resend.exceptions.ResendError as e:
        log(f"Resend error: {e}", "ERROR")
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
  python run.py --validate --json  # Test RSS feeds with JSON output
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and generate only (no email, no DB record)")
    parser.add_argument("--no-email", action="store_true", help="Skip sending email (still records to DB)")
    parser.add_argument("--no-record", action="store_true", help="Skip recording to DB (still sends email)")
    parser.add_argument(
        "--select-only", action="store_true", help="Run Pass 1 only (selection) - creates selections.json"
    )
    parser.add_argument(
        "--write-only", action="store_true", help="Run Pass 2 only (writing) - uses existing selections.json"
    )
    parser.add_argument(
        "--send-only",
        action="store_true",
        help="Send latest digest without fetching/generating (for retrying after failure)",
    )
    parser.add_argument("--preview", action="store_true", help="Open latest digest in browser (no-op in Docker)")
    parser.add_argument("--test-email", metavar="EMAIL", help="Send test email to specified address and exit")
    parser.add_argument("--validate", action="store_true", help="Test all RSS feeds and report health status")
    parser.add_argument("--json", action="store_true", help="Output in JSON format (use with --validate)")
    parser.add_argument("--health-check", action="store_true", help="Verify Claude auth is working (for monitoring)")
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
        return validate_feeds(sources, json_output=args.json)

    # Health check mode - verify Claude auth
    if args.health_check:
        log("Running Claude auth health check...")
        result = subprocess.run(
            ["claude", "-p", "respond with 'ok'", "--max-turns", "1"], capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and "ok" in result.stdout.lower():
            log("Health check passed: Claude auth working")
            return 0
        else:
            log(f"Health check FAILED: returncode={result.returncode}", "ERROR")
            if result.stderr:
                log(f"stderr: {result.stderr[:500]}", "ERROR")
            return 1

    # Preview mode - open latest digest in browser
    if args.preview:
        digest = find_latest_digest()
        if not digest:
            log("No digest found to preview", "ERROR")
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
            log("No digest found to send", "ERROR")
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

    # Write-only mode - render HTML from existing selections
    if args.write_only:
        validate_env(dry_run=skip_email)
        init_db()
        selections = validate_selections()  # Ensure selections.json exists and is valid
        digest = write_digest_from_selections(selections)
        replace_placeholders(digest, extract_preheader(selections))
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

    # Pass 1: Select stories (Claude)
    generate_selections()
    selections = validate_selections()

    # Select-only mode - stop after Pass 1
    if args.select_only:
        log("Select-only mode: stopping after Pass 1")
        return 0

    # Pass 2: Render HTML digest (Python - no Claude)
    digest = write_digest_from_selections(selections)
    replace_placeholders(digest, extract_preheader(selections))

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
            log("No headlines recorded - Claude may not have generated shown_headlines.json", "WARN")
        record_shown_headlines(shown_headlines)
        record_run(articles_fetched, articles_emailed=recipients)

    # Clean up shown_headlines.json only after successful completion
    cleanup_shown_headlines()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted", "WARN")
        sys.exit(130)
    except Exception as e:
        log(f"{type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
