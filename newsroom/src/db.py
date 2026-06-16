"""Database operations for digest runs, headlines, and source health."""

import logging
import os
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
    recording: bool = False
    broadcasting: bool = False
    alerting: bool = False


_state = _State()


def _db_path() -> Path:
    """Return db_path, raising if init() hasn't been called."""
    if _state.db_path is None:
        raise RuntimeError("db.init() must be called before database operations")
    return _state.db_path


def init(db_path: Path, migrations_dir: Path, *, apply_migrations: bool = True):
    """Initialize database, auto-applying any pending migrations.

    ``apply_migrations=False`` skips the migration step and only records the
    path -- used by the read-only dead-man's-switch (--verify-today) so a
    verification probe never writes to (migrates) the production database.
    """
    db_path.parent.mkdir(exist_ok=True)
    if apply_migrations:
        _apply_pending_migrations(db_path, migrations_dir)
    _state.db_path = db_path


def _apply_pending_migrations(db_path: Path, migrations_dir: Path):
    """Apply any pending migrations, creating the database if needed."""
    from yoyo import get_backend, read_migrations

    backend = get_backend(f"sqlite:///{db_path}")
    migrations = read_migrations(str(migrations_dir))
    pending = backend.to_apply(migrations)

    if pending:
        logger.info("Applying %d pending migration(s)...", len(pending))
        backend.apply_migrations(pending)
        logger.info("Migrations applied")


def get_last_run_time() -> datetime | None:
    """Get timestamp of last completed digest run."""
    if not _state.db_path or not _state.db_path.exists():
        return None
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs WHERE completed_at IS NOT NULL")
            result = cursor.fetchone()[0]
            if result:
                return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=UTC)
    except sqlite3.Error as e:
        logger.error("DB error getting last run time: %s", e)
    return None


def start_run(*, recording: bool = True, broadcasting: bool = True, alerting: bool = True) -> int | None:
    """Start a digest run. Sets module-level flags that guard all subsequent writes."""
    _state.recording = recording
    _state.broadcasting = broadcasting
    _state.alerting = alerting

    if not recording:
        return None

    try:
        git_sha = os.environ.get("GIT_SHA")
        with sqlite3.connect(_db_path()) as conn:
            cursor = conn.execute(
                "INSERT INTO digest_runs (articles_fetched, articles_emailed, git_sha) VALUES (NULL, NULL, ?)",
                (git_sha,),
            )
            _state.run_id = cursor.lastrowid
            return _state.run_id
    except sqlite3.Error as e:
        logger.error("DB error starting run: %s", e)
        _state.run_id = None
        _state.recording = False
        return None


def complete_run(articles_fetched: int, articles_emailed: int = 0):
    """Complete a digest run by updating counts and marking completion time."""
    if not _state.recording or _state.run_id is None:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "UPDATE digest_runs SET articles_fetched = ?, articles_emailed = ?, completed_at = datetime('now', 'utc'), status = 'completed' WHERE id = ?",
                (articles_fetched, articles_emailed, _state.run_id),
            )
    except sqlite3.Error as e:
        logger.error("DB error completing run %d: %s", _state.run_id, e)


def abort_run(error: str | None = None):
    """Mark the current run failed (kept for forensics) and reset run state.

    Marks 'failed' rather than deleting so an already-delivered digest's record
    survives a post-send failure. See migration 20260616190000 for the dedup
    rationale.
    """
    if _state.run_id is not None:
        _fail_run(_state.run_id, error)
    _state.run_id = None
    _state.recording = False
    _state.broadcasting = False
    _state.alerting = False


def _fail_run(run_id: int, error: str | None):
    """Mark a run failed, preserving its rows. Best-effort: never raises."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "UPDATE digest_runs SET status = 'failed', error = ? WHERE id = ?",
                (error, run_id),
            )
        logger.info("Marked run %d failed", run_id)
    except sqlite3.Error as e:
        logger.error("DB error marking run %d failed: %s", run_id, e)


def has_completed_run_today(*, on_error: bool = True) -> bool:
    """Check if a completed run exists for today (UTC).

    ``on_error`` is the value returned when the DB can't be read. The
    duplicate-run guard fails closed (default True -> assume a run exists, do
    not double-run). The dead-man's-switch check (--verify-today) passes
    ``on_error=False`` so an unreadable DB surfaces as "no run" and triggers an
    alert rather than being silently masked.
    """
    if not _state.db_path or not _state.db_path.exists():
        return False
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM digest_runs WHERE date(run_at) = date('now') AND completed_at IS NOT NULL"
            )
            return cursor.fetchone()[0] > 0
    except sqlite3.Error as e:
        logger.error("DB error checking today's runs: %s", e)
        return on_error


def should_broadcast() -> bool:
    """Whether the current run should send the digest email."""
    return _state.broadcasting


def should_alert() -> bool:
    """Whether the current run should send health alert emails."""
    return _state.alerting


def get_previous_headlines(days: int = 7) -> list[dict]:
    """Get headlines shown in the last N days for deduplication.

    Returns RSS titles (original_title) when available, falling back to
    editorial headlines for comparison.
    """
    if not _state.db_path or not _state.db_path.exists():
        return []
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COALESCE(original_title, headline) as headline,
                       tier, date(shown_at) as date
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


def get_yesterday_digest_headlines() -> list[dict]:
    """Get editorial headlines from the most recent completed digest.

    Returns the actual headlines shown to readers (not RSS titles) for
    the SELECT subagent to avoid re-covering the same angles.
    """
    if not _state.db_path or not _state.db_path.exists():
        return []
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT headline, tier
                FROM shown_narratives
                WHERE run_id = (
                    SELECT id FROM digest_runs
                    WHERE completed_at IS NOT NULL
                    ORDER BY run_at DESC LIMIT 1
                )
                  AND tier IN ('must_know', 'should_know')
                ORDER BY tier, headline
            """,
            )
            return [{"headline": row[0], "tier": row[1]} for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error("DB error getting yesterday's headlines: %s", e)
        return []


def record_shown_headlines(headlines: list[dict]):
    """Record headlines that were shown in this digest.

    Stores both the editorial headline and the original RSS title for
    same-register deduplication.
    """
    if not _state.recording:
        return
    if not headlines or not isinstance(headlines[0], dict):
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                "INSERT INTO shown_narratives (headline, tier, source_id, original_title, cluster_id, run_id) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        h.get("headline", ""),
                        h.get("tier", ""),
                        h.get("source_id"),
                        h.get("original_title"),
                        h.get("cluster_id"),
                        _state.run_id,
                    )
                    for h in headlines
                ],
            )
    except sqlite3.Error as e:
        logger.error("DB error recording headlines: %s", e)


def record_source_health(results: list[tuple[str, bool, str | None, int, int]]):
    """Record source fetch results.

    Each tuple is (source_id, success, error_message, articles_fetched, articles_kept).
    """
    if not _state.recording:
        return
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
    if not _state.recording:
        return
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
    if not _state.recording:
        return
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
    if not _state.recording:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO selections (run_id, selections_json) VALUES (?, ?)",
                (_state.run_id, selections_json),
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving selections: %s", e)


def archive_clusters(clusters_json: str):
    """Archive the per-run clusters.json blob for historical analysis.

    clusters.json is overwritten every run; persisting it keyed by run makes
    historical cluster composition queryable alongside shown_narratives.cluster_id.
    """
    if not _state.recording:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO cluster_runs (run_id, clusters_json) VALUES (?, ?)",
                (_state.run_id, clusters_json),
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving clusters: %s", e)


# Per-run intermediates in claude_input/ that are overwritten every run.
# Persisting them keyed by run gives a durable, reproducible trace that survives
# volume wipes/redeploys and grows the eval golden set over time.
_TRACE_ARTIFACTS = (
    "clusters.json",
    "selected.json",
    "draft_selections.json",
    "coherence_report.json",
    "article_index.json",
    "selections.json",
    "recap.txt",
)


def archive_run_artifacts(claude_input_dir: Path, models: dict[str, str] | None = None):
    """Archive the per-run intermediate trace files for reproducibility.

    Reads each known intermediate that exists under claude_input_dir and inserts
    one row per artifact (artifact_name = filename, content = file text). If
    `models` is supplied, the resolved model IDs are stored as a synthetic
    "models.json" artifact.

    Fails soft: a trace-archival problem must never kill a digest, so all errors
    are logged and swallowed.
    """
    if not _state.recording or _state.run_id is None:
        return
    try:
        rows: list[tuple[int, str, str]] = []
        # Fixed intermediates plus the dynamically-numbered articles_*.csv -- the
        # post-dedup article text (titles + summaries) Claude actually curated
        # from. Without the CSVs the archive has the ID index (article_index.json)
        # but not what Claude read, so a run couldn't be reproduced or re-graded.
        paths = [claude_input_dir / name for name in _TRACE_ARTIFACTS]
        paths += sorted(claude_input_dir.glob("articles_*.csv"))
        for path in paths:
            if not path.exists():
                continue
            try:
                rows.append((_state.run_id, path.name, path.read_text()))
            except OSError as e:
                logger.warning("Could not read trace artifact %s: %s", path.name, e)
        if models:
            import json

            rows.append((_state.run_id, "models.json", json.dumps(models, sort_keys=True)))
        if not rows:
            return
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)",
                rows,
            )
        logger.info("Archived %d trace artifact(s) for run %d", len(rows), _state.run_id)
    except Exception as e:
        # Deliberately broad: trace archival is best-effort and must never crash
        # a digest (e.g. a non-UTF-8 artifact would raise UnicodeDecodeError).
        logger.error("Error archiving run artifacts: %s", e)


def get_run_artifacts(run_id: int) -> dict[str, str]:
    """Return a run's archived trace as {artifact_name: content} for reproduction."""
    if not _state.db_path or not _state.db_path.exists():
        return {}
    try:
        with sqlite3.connect(_state.db_path) as conn:
            cursor = conn.execute(
                "SELECT artifact_name, content FROM run_artifacts WHERE run_id = ? ORDER BY artifact_name",
                (run_id,),
            )
            return dict(cursor.fetchall())
    except sqlite3.Error as e:
        logger.error("DB error getting run artifacts for %d: %s", run_id, e)
        return {}


def record_usage(usage_rows: list[dict]):
    """Record per-subagent token usage for the current run.

    Each dict has: subagent, model, input_tokens, output_tokens,
    cache_write_tokens, cache_read_tokens, api_cost_usd.
    """
    if not _state.recording or _state.run_id is None:
        return
    if not usage_rows:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.executemany(
                """INSERT INTO run_usage
                   (run_id, subagent, model, input_tokens, output_tokens,
                    cache_write_tokens, cache_read_tokens, api_cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        _state.run_id,
                        r["subagent"],
                        r["model"],
                        r["input_tokens"],
                        r["output_tokens"],
                        r["cache_write_tokens"],
                        r["cache_read_tokens"],
                        r["api_cost_usd"],
                    )
                    for r in usage_rows
                ],
            )
        logger.info("Recorded %d usage rows for run %d", len(usage_rows), _state.run_id)
    except sqlite3.Error as e:
        logger.error("DB error recording usage: %s", e)


def prepare_for_web(html_str: str) -> str:
    """Strip email-only elements from digest HTML for web serving."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_str, "html.parser")

    for sel in ("nav.header-links", "div.feedback"):
        for el in soup.select(sel):
            el.decompose()

    # Remove footer paragraphs containing an Unsubscribe link
    for a in soup.select("a"):
        if a.string == "Unsubscribe":
            p = a.find_parent("p")
            if p:
                p.decompose()

    return str(soup)


def save_digest(digest_path: Path, preheader: str = ""):
    """Save digest HTML to database for web serving."""
    if not _state.recording:
        return
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


def get_broadcast(date_str: str) -> tuple[str | None, str | None] | None:
    """Return (broadcast_id, broadcast_status) for a date's digest, or None.

    None means there is no digest row for that date yet (so nothing was sent).
    A row with NULL broadcast_id means the digest was saved but never broadcast.
    Used to make delivery idempotent: a retry checks this before sending again.

    Deliberately does NOT swallow DB errors: "I can't read the broadcast state"
    must not look like "nothing was sent" (that would invite a double-send), so a
    read failure raises and the caller fails closed.
    """
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT broadcast_id, broadcast_status FROM digests WHERE date = ?",
            (date_str,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def record_broadcast(date_str: str, broadcast_id: str | None, status: str | None):
    """Persist the Resend broadcast id + status against a date's digest row.

    The digest row (save_digest) must already exist -- broadcast always follows
    save in every pipeline path. No-op when not recording (dry runs).
    """
    if not _state.recording:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            cursor = conn.execute(
                "UPDATE digests SET broadcast_id = ?, broadcast_status = ? WHERE date = ?",
                (broadcast_id, status, date_str),
            )
        if cursor.rowcount == 0:
            # No digest row to attach to -> the idempotency state was NOT persisted.
            # That silently reopens the double-send risk, so make it loud.
            logger.error(
                "record_broadcast: no digest row for %s; broadcast %s (%s) state NOT persisted",
                date_str,
                broadcast_id,
                status,
            )
    except sqlite3.Error as e:
        logger.error("DB error recording broadcast for %s: %s", date_str, e)


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
                "run_usage",
                "cluster_runs",
                "run_artifacts",
            ]:
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM digest_runs WHERE id = ?", (run_id,))
        logger.info("Deleted run %d and all associated data", run_id)
    except sqlite3.Error as e:
        logger.error("DB error deleting run %d: %s", run_id, e)
