"""Database operations for digest runs, headlines, and source health.

All functions take db_path explicitly - no module-level global state.
"""

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def check_pending_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    """Return list of pending migration IDs. Empty list if all applied."""
    from yoyo import get_backend, read_migrations

    backend = get_backend(f"sqlite:///{db_path}")
    migrations = read_migrations(str(migrations_dir))
    pending = backend.to_apply(migrations)

    return [m.id for m in pending]


def init_db(db_path: Path, migrations_dir: Path):
    """Verify database exists and schema is current. Schema managed by bin/migrate."""
    db_path.parent.mkdir(exist_ok=True)

    if not db_path.exists():
        raise RuntimeError("Database not found. Run: bin/migrate")

    pending = check_pending_migrations(db_path, migrations_dir)
    if pending:
        raise RuntimeError(f"Pending migrations: {', '.join(pending)}\nRun: bin/migrate")


def get_last_run_time(db_path: Path) -> datetime | None:
    """Get timestamp of last digest run."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs")
            result = cursor.fetchone()[0]
            if result:
                return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=UTC)
    except sqlite3.Error:
        pass
    return None


def start_run(db_path: Path) -> int | None:
    """Start a digest run, returning run_id for archival. Update with complete_run() when done."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("INSERT INTO digest_runs (articles_fetched, articles_emailed) VALUES (NULL, NULL)")
            return cursor.lastrowid
    except sqlite3.Error:
        return None


def complete_run(db_path: Path, run_id: int, articles_fetched: int, articles_emailed: int = 0):
    """Complete a digest run by updating counts."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE digest_runs SET articles_fetched = ?, articles_emailed = ? WHERE id = ?",
                (articles_fetched, articles_emailed, run_id),
            )
    except sqlite3.Error:
        pass


def record_run(db_path: Path, articles_fetched: int, articles_emailed: int = 0) -> int | None:
    """Record a successful digest run. Returns run ID or None on error."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO digest_runs (articles_fetched, articles_emailed) VALUES (?, ?)",
                (articles_fetched, articles_emailed),
            )
            return cursor.lastrowid
    except sqlite3.Error:
        return None


def get_previous_headlines(db_path: Path, days: int = 7) -> list[dict]:
    """Get headlines shown in the last N days for deduplication."""
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
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
    except sqlite3.Error:
        return []


def record_shown_headlines(db_path: Path, headlines: list[dict]):
    """Record headlines that were shown in this digest."""
    if not headlines:
        return
    # Validate format before processing
    if headlines and not isinstance(headlines[0], dict):
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier, source_id) VALUES (?, ?, ?)",
                [(h.get("headline", ""), h.get("tier", ""), h.get("source_id")) for h in headlines],
            )
    except sqlite3.Error:
        pass


def record_source_health(db_path: Path, results: list[tuple[str, bool, str | None, int, int]]):
    """Record source fetch results.

    Each tuple is (source_id, success, error_message, articles_fetched, articles_kept).
    """
    if not results:
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "INSERT INTO source_health (source_id, success, error_message, articles_fetched, articles_kept) VALUES (?, ?, ?, ?, ?)",
                results,
            )
    except sqlite3.Error:
        pass


def get_consecutive_failures(db_path: Path, source_id: str, limit: int = 10) -> int:
    """Get count of consecutive recent failures for a source."""
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
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
    except sqlite3.Error:
        return 0


def get_failing_sources(db_path: Path, min_consecutive: int = 3) -> list[tuple[str, int]]:
    """Get sources with N+ consecutive failures. Returns [(source_id, failure_count)]."""
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT source_id FROM source_health
                WHERE recorded_at > datetime('now', '-7 days')
            """)
            source_ids = [row[0] for row in cursor]
    except sqlite3.Error:
        return []

    failing = [
        (sid, count) for sid in source_ids if (count := get_consecutive_failures(db_path, sid)) >= min_consecutive
    ]
    return sorted(failing, key=lambda x: -x[1])


def log_dedup_action(
    db_path: Path,
    article_title: str,
    article_source_id: str | None,
    matched_headline: str,
    similarity: float,
    threshold: float,
    action: str,
):
    """Log a dedup decision to the database."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO dedup_log
                   (article_title, article_source_id, matched_headline, similarity, threshold, action)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_title, article_source_id, matched_headline, similarity, threshold, action),
            )
    except sqlite3.Error:
        pass


def archive_articles(db_path: Path, run_id: int | None, articles: list[dict]):
    """Archive all fetched articles for historical analysis.

    Args:
        db_path: Path to SQLite database
        run_id: Run ID from start_run(), or None to archive without linking
        articles: List of dicts with source_id, title, url, published, summary
    """
    if not articles:
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                """INSERT INTO fetched_articles (run_id, source_id, title, url, published, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, a["source_id"], a["title"], a["url"], a.get("published"), a.get("summary"))
                    for a in articles
                ],
            )
    except sqlite3.Error:
        pass


def archive_selections(db_path: Path, run_id: int | None, selections_json: str):
    """Archive Claude's raw selection output for historical analysis."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO selections (run_id, selections_json) VALUES (?, ?)",
                (run_id, selections_json),
            )
    except sqlite3.Error:
        pass


def prepare_for_web(html: str) -> str:
    """Strip email-only elements from digest HTML for web serving."""
    # Remove header links nav (Subscribe/View online - redundant on web)
    html = re.sub(r'(?s)\s*<nav class="header-links">.*?</nav>', "", html)
    # Legacy: Remove old "View in browser" link if present
    html = re.sub(r'<p class="view-in-browser">.*?</p>', "", html)
    # Remove "Past digests · Unsubscribe" footer line
    html = re.sub(r'<p><a href="[^"]*">Past digests</a>.*?Unsubscribe</a></p>', "", html)
    # Remove standalone Unsubscribe link (when no archive link)
    html = re.sub(r'<p><a href="[^"]*">Unsubscribe</a></p>', "", html)
    # Remove feedback buttons (mailto links don't work well on web)
    html = re.sub(r'(?s)<div class="feedback">.*?</div>\s*</div>', "", html)
    return html


def save_digest(db_path: Path, digest_path: Path, log_fn=None):
    """Save digest HTML to database for web serving."""
    # Extract date from filename (digest-YYYY-MM-DD*.html -> YYYY-MM-DD)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", digest_path.stem)
    if match:
        date_str = match.group(1)
    else:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        if log_fn:
            log_fn(f"Could not extract date from '{digest_path.stem}', using {date_str}", "WARN")

    html_content = digest_path.read_text()
    web_html = prepare_for_web(html_content)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO digests (date, html) VALUES (?, ?)", (date_str, web_html))
        if log_fn:
            log_fn(f"Saved digest to database: {date_str}")
    except sqlite3.Error as e:
        if log_fn:
            log_fn(f"DB error saving digest: {e}", "ERROR")
