"""Database operations for digest runs, headlines, and source health."""

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class _State:
    db_path: Path | None = None
    run_id: int | None = None


_state = _State()


def _db_path() -> Path:
    """Return db_path, raising if init() hasn't been called."""
    if _state.db_path is None:
        raise RuntimeError("db.init() must be called before database operations")
    return _state.db_path


def init(db_path: Path, migrations_dir: Path):
    """Initialize database and set module context."""
    _state.db_path = db_path
    db_path.parent.mkdir(exist_ok=True)

    if not db_path.exists():
        raise RuntimeError("Database not found. Run: bin/migrate")

    pending = check_pending_migrations(db_path, migrations_dir)
    if pending:
        raise RuntimeError(f"Pending migrations: {', '.join(pending)}\nRun: bin/migrate")


def check_pending_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    """Return list of pending migration IDs. Empty list if all applied."""
    from yoyo import get_backend, read_migrations

    backend = get_backend(f"sqlite:///{db_path}")
    migrations = read_migrations(str(migrations_dir))
    pending = backend.to_apply(migrations)

    return [m.id for m in pending]


def get_last_run_time() -> datetime | None:
    """Get timestamp of last digest run."""
    if not _state.db_path or not _state.db_path.exists():
        return None
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs")
            result = cursor.fetchone()[0]
            if result:
                return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=UTC)
    except sqlite3.Error as e:
        logger.error("DB error getting last run time: %s", e)
    return None


def start_run() -> int | None:
    """Start a digest run, returning run_id for archival. Update with complete_run() when done."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            cursor = conn.execute("INSERT INTO digest_runs (articles_fetched, articles_emailed) VALUES (NULL, NULL)")
            _state.run_id = cursor.lastrowid
            return _state.run_id
    except sqlite3.Error as e:
        logger.error("DB error starting run: %s", e)
        _state.run_id = None
        return None


def complete_run(run_id: int, articles_fetched: int, articles_emailed: int = 0):
    """Complete a digest run by updating counts."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "UPDATE digest_runs SET articles_fetched = ?, articles_emailed = ? WHERE id = ?",
                (articles_fetched, articles_emailed, run_id),
            )
    except sqlite3.Error as e:
        logger.error("DB error completing run %d: %s", run_id, e)


def get_previous_headlines(days: int = 7) -> list[dict]:
    """Get headlines shown in the last N days for deduplication."""
    if not _state.db_path or not _state.db_path.exists():
        return []
    try:
        with sqlite3.connect(_state.db_path) as conn:
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
        logger.error("DB error getting previous headlines: %s", e)
        return []


def record_shown_headlines(headlines: list[dict]):
    """Record headlines that were shown in this digest."""
    if not headlines or not isinstance(headlines[0], dict):
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier, source_id, run_id) VALUES (?, ?, ?, ?)",
                [(h.get("headline", ""), h.get("tier", ""), h.get("source_id"), _state.run_id) for h in headlines],
            )
    except sqlite3.Error as e:
        logger.error("DB error recording headlines: %s", e)


def record_source_health(results: list[tuple[str, bool, str | None, int, int]]):
    """Record source fetch results.

    Each tuple is (source_id, success, error_message, articles_fetched, articles_kept).
    """
    if not results:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                "INSERT INTO source_health (source_id, success, error_message, articles_fetched, articles_kept, run_id) VALUES (?, ?, ?, ?, ?, ?)",
                [(*r, _state.run_id) for r in results],
            )
    except sqlite3.Error as e:
        logger.error("DB error recording source health: %s", e)


def get_consecutive_failures(source_id: str, limit: int = 10) -> int:
    """Get count of consecutive recent failures for a source."""
    if not _state.db_path or not _state.db_path.exists():
        return 0
    try:
        with sqlite3.connect(_state.db_path) as conn:
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
        logger.error("DB error getting failures for %s: %s", source_id, e)
        return 0


def get_failing_sources(min_consecutive: int = 3) -> list[tuple[str, int]]:
    """Get sources with N+ consecutive failures. Returns [(source_id, failure_count)]."""
    if not _state.db_path or not _state.db_path.exists():
        return []
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT source_id FROM source_health
                WHERE recorded_at > datetime('now', '-7 days')
            """)
            source_ids = [row[0] for row in cursor]
    except sqlite3.Error as e:
        logger.error("DB error getting failing sources: %s", e)
        return []

    failing = [(sid, count) for sid in source_ids if (count := get_consecutive_failures(sid)) >= min_consecutive]
    return sorted(failing, key=lambda x: -x[1])


def log_dedup_action(
    article_title: str,
    article_source_id: str | None,
    matched_headline: str,
    similarity: float,
    threshold: float,
    action: str,
):
    """Log a dedup decision to the database."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                """INSERT INTO dedup_log
                   (article_title, article_source_id, matched_headline, similarity, threshold, action, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (article_title, article_source_id, matched_headline, similarity, threshold, action, _state.run_id),
            )
    except sqlite3.Error as e:
        logger.error("DB error logging dedup action: %s", e)


def archive_articles(articles: list[dict]):
    """Archive all fetched articles for historical analysis."""
    if not articles:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                """INSERT INTO fetched_articles (run_id, source_id, title, url, published, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (_state.run_id, a["source_id"], a["title"], a["url"], a.get("published"), a.get("summary"))
                    for a in articles
                ],
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving %d articles: %s", len(articles), e)


def archive_selections(selections_json: str):
    """Archive Claude's raw selection output for historical analysis."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO selections (run_id, selections_json) VALUES (?, ?)",
                (_state.run_id, selections_json),
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving selections: %s", e)


def prepare_for_web(html: str) -> str:
    """Strip email-only elements from digest HTML for web serving."""
    html = re.sub(r'(?s)\s*<nav class="header-links">.*?</nav>', "", html)
    html = re.sub(r'<p class="view-in-browser">.*?</p>', "", html)
    html = re.sub(r'<p><a href="[^"]*">Past digests</a>.*?Unsubscribe</a></p>', "", html)
    html = re.sub(r'<p><a href="[^"]*">Unsubscribe</a></p>', "", html)
    html = re.sub(r'(?s)<div class="feedback">.*?</div>\s*</div>', "", html)
    return html


def save_digest(digest_path: Path, preheader: str = ""):
    """Save digest HTML to database for web serving."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", digest_path.stem)
    if match:
        date_str = match.group(1)
    else:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        logger.warning("Could not extract date from '%s', using %s", digest_path.stem, date_str)

    html_content = digest_path.read_text()
    web_html = prepare_for_web(html_content)

    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                """INSERT INTO digests (date, html, preheader, run_id) VALUES (?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                     html = excluded.html,
                     preheader = CASE WHEN excluded.preheader = '' THEN digests.preheader ELSE excluded.preheader END,
                     run_id = excluded.run_id""",
                (date_str, web_html, preheader, _state.run_id),
            )
        logger.info("Saved digest to database: %s", date_str)
    except sqlite3.Error as e:
        logger.error("DB error saving digest: %s", e)


def delete_run(run_id: int):
    """Delete a run and all associated data across all tables."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            for table in [
                "fetched_articles",
                "selections",
                "shown_narratives",
                "source_health",
                "dedup_log",
                "digests",
            ]:
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM digest_runs WHERE id = ?", (run_id,))
        logger.info("Deleted run %d and all associated data", run_id)
    except sqlite3.Error as e:
        logger.error("DB error deleting run %d: %s", run_id, e)
