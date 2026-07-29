"""Database operations for digest runs, headlines, and source health."""

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from config import THREADS_ENABLED

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


def _connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with hardening pragmas applied per-connection.

    - ``foreign_keys=ON``: SQLite leaves FK enforcement OFF by default, so the
      run_id foreign keys added in migrations are otherwise silently unenforced.
    - ``busy_timeout``: wait out a concurrent writer instead of erroring
      immediately with "database is locked" -- the web server (circulation) and
      the batch pipeline share this one file.

    WAL journal mode is deliberately NOT set here: it would leave uncommitted
    data in a ``-wal`` sidecar that ``bin/db-clone``'s raw copy of the ``.db``
    file would miss. Enable it only together with a WAL-aware clone/checkpoint.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def is_recording() -> bool:
    """Whether this run persists to the DB (False under --dry-run/--no-record)."""
    return _state.recording


def current_run_id() -> int | None:
    """The active run's id, or None if no run has started."""
    return _state.run_id


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
        with _connect(_state.db_path) as conn:
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
        with _connect(_db_path()) as conn:
            cursor = conn.execute(
                "INSERT INTO digest_runs (articles_kept, articles_emailed, git_sha) VALUES (NULL, NULL, ?)",
                (git_sha,),
            )
            _state.run_id = cursor.lastrowid
            return _state.run_id
    except sqlite3.Error as e:
        logger.error("DB error starting run: %s", e)
        _state.run_id = None
        _state.recording = False
        return None


def complete_run(articles_kept: int, articles_emailed: int = 0):
    """Complete a digest run by updating counts and marking completion time.

    ``articles_kept`` is the count of articles kept after dedup/filtering (the
    column was historically misnamed ``articles_fetched``; see migration
    20260619000000, which renamed it). It is a deliberate per-run denormalization
    of ``SUM(source_health.articles_kept)`` -- a durable snapshot that does not
    depend on source_health row/FK completeness for historical display.
    """
    if not _state.recording or _state.run_id is None:
        return
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                "UPDATE digest_runs SET articles_kept = ?, articles_emailed = ?, completed_at = datetime('now', 'utc'), status = 'completed' WHERE id = ?",
                (articles_kept, articles_emailed, _state.run_id),
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
        with _connect(_db_path()) as conn:
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
        with _connect(_state.db_path) as conn:
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
        with _connect(_state.db_path) as conn:
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
        with _connect(_state.db_path) as conn:
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


def _warn_if_recent_headlines_missing(conn: sqlite3.Connection, window: str, days: int) -> None:
    """Tell "no history yet" apart from "the query stopped matching".

    A tier rename, a completed_at regression, or a run_id backfill gap all make
    get_recent_digest_headlines match nothing while staying valid SQL -- there is
    no sqlite3.Error to catch, and the empty result is byte-identical to a fresh
    install, so WRITE would silently lose continuation context.
    """
    completed = conn.execute(
        "SELECT COUNT(*) FROM digest_runs WHERE completed_at IS NOT NULL AND date(run_at) >= date('now', ?)",
        (window,),
    ).fetchone()[0]
    if completed:
        logger.warning(
            "recent digest headlines: 0 rows despite %d completed run(s) in the last %d days "
            "-- WRITE loses continuation context",
            completed,
            days,
        )
    else:
        logger.info("recent digest headlines: no completed runs in window (expected on a new install)")


def get_recent_digest_headlines(days: int = 7) -> list[dict]:
    """Editorial headlines readers were actually shown over the last `days`.

    Wider than get_yesterday_digest_headlines (which SELECT uses) because this
    feeds WRITE, and the observed re-ship gaps run +1d through +7d -- a
    yesterday-only window misses half of them.

    Only COMPLETED runs count: an aborted run's headlines never reached anyone,
    so treating them as "already told the reader" would suppress a story nobody
    has seen. Deduped by headline because shown_narratives stores one row PER
    SOURCE, not per story (mean 2.10, max 48), which would otherwise repeat a
    well-sourced story a dozen times in the prompt.
    """
    if not _state.db_path or not _state.db_path.exists():
        return []
    window = f"-{days} days"
    try:
        with _connect(_state.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT sn.headline, sn.tier, MAX(dr.run_at) AS last_shown
                FROM shown_narratives sn
                JOIN digest_runs dr ON dr.id = sn.run_id
                WHERE dr.completed_at IS NOT NULL
                  AND date(dr.run_at) >= date('now', ?)
                  AND sn.tier IN ('must_know', 'should_know')
                  AND sn.headline IS NOT NULL AND sn.headline != ''
                GROUP BY sn.headline
                ORDER BY last_shown DESC
            """,
                (window,),
            )
            # `tier` rides SQLite's bare-column rule: it comes from the row that produced
            # MAX(run_at), which is ONLY defined while the statement has exactly one
            # min()/max() aggregate -- hence the `last_shown` alias in ORDER BY rather
            # than a second MAX(). Adding another min/max (e.g. a "first shown" column)
            # silently starts returning an arbitrary row's tier, with no error.
            # test_tier_and_date_come_from_the_newest_showing is the guard.
            rows = cursor.fetchall()
            if not rows:
                _warn_if_recent_headlines_missing(conn, window, days)
            # last_shown is `YYYY-MM-DD HH:MM:SS` (run_at's column default is
            # datetime('now','utc')). Sliced here rather than wrapped in SQL date() so
            # the ORDER BY above keeps full intra-day precision.
            return [{"headline": row[0], "tier": row[1], "date": row[2][:10]} for row in rows]
    except sqlite3.Error as e:
        logger.error("DB error getting recent digest headlines: %s", e)
        return []


def get_issue_number(date_str: str) -> int | None:
    """Sequential edition number ("No. N") for the digest dated ``date_str``.

    Mirrors circulation's archive rank (``SELECT COUNT(*) FROM digests WHERE
    date <= d.date``): once a digest row exists its rank equals that count. At
    render time the current digest usually is NOT stored yet (save_digest runs
    after replace_placeholders), so we add 1 to give the forthcoming issue its
    number; on a same-day re-render the row already exists and the count is
    already correct. Returns None when the DB is unavailable so render can fall
    back to a placeholder rather than crash.
    """
    if not _state.db_path or not _state.db_path.exists():
        return None
    try:
        with _connect(_state.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM digests WHERE date <= ?", (date_str,)).fetchone()[0]
            already_stored = conn.execute("SELECT 1 FROM digests WHERE date = ? LIMIT 1", (date_str,)).fetchone()
            return total if already_stored else total + 1
    except sqlite3.Error as e:
        logger.error("DB error computing issue number: %s", e)
        return None


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
        with _connect(_db_path()) as conn:
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
        with _connect(_db_path()) as conn:
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
        with _connect(_state.db_path) as conn:
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
        with _connect(_state.db_path) as conn:
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


def get_run_health(run_id: int) -> dict:
    """Counts a finished run is judged on, for ``run_health.violations``.

    The shipped/stages/artifacts counts mirror
    ``analytics/queries/run-reliability.sql`` so the alert and the hand-run audit
    cannot disagree. The thread counts have no counterpart there.

    A continuation is an installment the LINKER matched (``matched_score`` set),
    not one that merely sits on a thread with an earlier run. The two differ
    exactly where it matters: run 244's 16 installments all recorded no match --
    the incident -- but ``bin/repair-threads`` later merged five of those threads
    into older ones, so the structural definition now scores that run as five
    continuations and hides the failure in its own audit trail.

    ``threads_available`` counts LIVE threads seen before this run. An all-time
    count never returns to zero, so it would stop being a guard the moment the
    system had any history.

    ``recipients`` stays None when unknown. The resend-existing-draft path records
    a delivered broadcast without a count, and collapsing that to 0 would claim a
    successful recovery had been "sent to nobody".

    Returns {} when the DB is unavailable; the caller treats that as "cannot
    judge" and stays quiet rather than alerting on missing data.
    """
    if not _state.db_path or not _state.db_path.exists():
        return {}
    try:
        with _connect(_state.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                  (SELECT COUNT(DISTINCT headline) FROM shown_narratives WHERE run_id = :r),
                  (SELECT COUNT(DISTINCT subagent)  FROM run_usage       WHERE run_id = :r),
                  (SELECT COUNT(*)                  FROM run_artifacts   WHERE run_id = :r),
                  (SELECT SUM(broadcast_recipients) FROM digests WHERE run_id = :r),
                  (SELECT COUNT(*) FROM thread_installments
                     WHERE run_id = :r AND matched_score IS NOT NULL),
                  (SELECT COUNT(*) FROM threads t
                     WHERE t.status = 'active'
                       AND EXISTS (SELECT 1 FROM thread_installments p
                                    WHERE p.thread_id = t.id AND p.run_id < :r)),
                  (SELECT CASE WHEN json_valid(content)
                               THEN json_extract(content, '$.batches_lost') END
                     FROM run_artifacts
                    WHERE run_id = :r AND artifact_name = 'cluster_health.json'),
                  (SELECT CASE WHEN json_valid(content)
                               THEN json_extract(content, '$.title_only_fallback') END
                     FROM run_artifacts
                    WHERE run_id = :r AND artifact_name = 'cluster_health.json')
                """,
                {"r": run_id},
            ).fetchone()
    except sqlite3.Error as e:
        logger.error("DB error getting run health for run %s: %s", run_id, e)
        return {}

    return {
        "run_id": run_id,
        "shipped": row[0],
        "stages": row[1],
        "artifacts": row[2],
        "recipients": row[3],
        "thread_continuations": row[4],
        "threads_available": row[5],
        "broadcasting": _state.broadcasting,
        # Config, not DB state: with the thread layer switched off no installments
        # are written at all, and the live threads that remain would otherwise make
        # the continuity rule fire on every run forever.
        "threads_enabled": THREADS_ENABLED,
        # None when the artifact is absent (runs archived before it existed, or a
        # stage that died before writing it) or unreadable. The rule treats that as
        # "cannot judge" rather than as a clean run.
        #
        # json_valid guards the extract because json_extract RAISES on malformed
        # input, and that raise is caught by this function's blanket sqlite3.Error
        # handler -- which returns {} and makes the CALLER skip every invariant.
        # A half-written observability file (an ENOSPC mid write_text) would
        # otherwise silently disable the monitor it was added to feed.
        "batches_lost": row[6],
        # Reported, not triggered on: the trigger is a wholesale batch loss, but a
        # short final batch means "a batch was lost" can be 1 article, not 40, and
        # the alert should say which.
        "title_only_fallback": row[7],
    }


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
        with _connect(_db_path()) as conn:
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
        with _connect(_db_path()) as conn:
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


def archive_selections(selections_json: str) -> bool:
    """Archive Claude's raw selection output for historical analysis.

    Fail-soft: a trace write must never block a delivered digest. Returns True on
    success (or when not recording -- nothing to do), False if the write failed,
    so callers can surface a persistent archival problem without aborting.
    """
    if not _state.recording:
        return True
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO selections (run_id, selections_json) VALUES (?, ?)",
                (_state.run_id, selections_json),
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving selections: %s", e)
        return False
    return True


def archive_clusters(clusters_json: str) -> bool:
    """Archive the per-run clusters.json blob for historical analysis.

    clusters.json is overwritten every run; persisting it keyed by run makes
    historical cluster composition queryable alongside shown_narratives.cluster_id.
    Fail-soft: returns False on a failed write (see archive_selections).
    """
    if not _state.recording:
        return True
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO cluster_runs (run_id, clusters_json) VALUES (?, ?)",
                (_state.run_id, clusters_json),
            )
    except sqlite3.Error as e:
        logger.error("DB error archiving clusters: %s", e)
        return False
    return True


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
    # Written by the extract-join stage. Its counts exist nowhere else the DB can
    # see -- they were log lines only -- so run_health could not judge a degraded
    # clustering run at all.
    "cluster_health.json",
    # Context files handed TO the stages rather than produced by them. Without these
    # the archive shows the headline that shipped but not the prior headlines SELECT
    # and WRITE were shown against, and claude_input/ is rmtree'd next run.
    "recent_digest_headlines.txt",
    "yesterday_headlines.txt",
)


def archive_run_artifacts(claude_input_dir: Path, models: dict[str, str] | None = None):
    """Archive the per-run intermediate trace files for reproducibility.

    Reads each known intermediate that exists under claude_input_dir and inserts
    one row per artifact (artifact_name = filename, content = file text). If
    `models` is supplied, the resolved model IDs are stored as a synthetic
    "models.json" artifact.

    Fails soft: a trace-archival problem must never kill a digest, so all errors
    are logged and reported via the return value (False) rather than raised (see
    archive_selections).
    """
    if not _state.recording or _state.run_id is None:
        return True
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
            return True
        with _connect(_db_path()) as conn:
            conn.executemany(
                "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)",
                rows,
            )
        logger.info("Archived %d trace artifact(s) for run %d", len(rows), _state.run_id)
    except Exception as e:
        # Deliberately broad: trace archival is best-effort and must never crash
        # a digest (e.g. a non-UTF-8 artifact would raise UnicodeDecodeError).
        logger.error("Error archiving run artifacts: %s", e)
        return False
    return True


def record_run_artifact(name: str, content: str) -> bool:
    """Archive ONE trace artifact produced mid-run, outside the file sweep.

    ``archive_run_artifacts`` takes its snapshot of claude_input/ before the thread path runs
    (run.py:146 vs :188), so a trace produced later cannot ride it -- written as a file it
    would simply never be picked up, and the gap would look exactly like a working feature.
    This is the seam for those: one row, same table, same fail-soft contract.
    """
    if not _state.recording or _state.run_id is None:
        return True
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)",
                (_state.run_id, name, content),
            )
    except Exception as e:
        # Broad by design: a trace row must never crash a digest (see archive_run_artifacts).
        logger.error("Error recording run artifact %s: %s", name, e)
        return False
    return True


def get_run_artifacts(run_id: int) -> dict[str, str]:
    """Return a run's archived trace as {artifact_name: content} for reproduction."""
    if not _state.db_path or not _state.db_path.exists():
        return {}
    try:
        with _connect(_state.db_path) as conn:
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
    cache_write_tokens, cache_read_tokens, api_cost_usd, and (optionally)
    duration_ms (per-stage wall-clock latency; NULL if the row omits it).
    """
    if not _state.recording or _state.run_id is None:
        return
    if not usage_rows:
        return
    try:
        with _connect(_db_path()) as conn:
            conn.executemany(
                """INSERT INTO run_usage
                   (run_id, subagent, model, input_tokens, output_tokens,
                    cache_write_tokens, cache_read_tokens, api_cost_usd, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        r.get("duration_ms"),
                    )
                    for r in usage_rows
                ],
            )
        logger.info("Recorded %d usage rows for run %d", len(usage_rows), _state.run_id)
    except sqlite3.Error as e:
        logger.error("DB error recording usage: %s", e)


def prepare_for_web(html_str: str) -> str:
    """Physically strip email-only elements from digest HTML before storing it
    for web serving.

    This physical strip is the sole mechanism keeping email-only content off the
    web archive -- circulation no longer ships an ``.email-only``/``.web-only``
    visibility flip. The stored blob never carries the per-recipient
    ``{{{RESEND_UNSUBSCRIBE_URL}}}`` merge tag, the view-in-browser line, the
    static email source line, or the inbox-preview preheader. Web-only surfaces
    (e.g. the source ``<details>``) render by default -- the redesigned template
    marks them normally rather than hiding them behind a flip.
    """
    from bs4 import BeautifulSoup, Comment

    soup = BeautifulSoup(html_str, "html.parser")

    # Drop every email-only node plus the preheader/spacer (inbox-preview only).
    for el in soup.select(".email-only, .preheader"):
        el.decompose()

    # Drop MSO conditional comments (the Outlook-only width wrapper) -- inert on
    # the web and just noise in the archive source.
    for c in soup.find_all(string=lambda t: isinstance(t, Comment) and "[if mso" in t):
        c.extract()

    return str(soup)


def digest_date(digest_path: Path) -> str:
    """The YYYY-MM-DD a digest is keyed by, from its filename (else today UTC).

    Single source of truth for the digests.date key, so save_digest and the
    delivery-idempotency lookups can never disagree on which row they touch.
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", digest_path.stem)
    return match.group(1) if match else datetime.now(UTC).strftime("%Y-%m-%d")


def save_digest(digest_path: Path, preheader: str = ""):
    """Save digest HTML to database for web serving."""
    if not _state.recording:
        return
    date_str = digest_date(digest_path)
    if not re.search(r"(\d{4}-\d{2}-\d{2})", digest_path.stem):
        logger.warning("Could not extract date from '%s', using %s", digest_path.stem, date_str)

    html_content = digest_path.read_text()
    web_html = prepare_for_web(html_content)

    try:
        with _connect(_db_path()) as conn:
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
    with _connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT broadcast_id, broadcast_status FROM digests WHERE date = ?",
            (date_str,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def record_broadcast(date_str: str, broadcast_id: str | None, status: str | None, recipients: int | None = None):
    """Persist the Resend broadcast id + status against a date's digest row.

    ``recipients`` is preserved when None (COALESCE), so a later status-only
    update (e.g. 'created' -> 'sent') does not wipe a recorded count. The digest
    row (save_digest) must already exist -- broadcast always follows save in
    every pipeline path. No-op when not recording (dry runs).
    """
    if not _state.recording:
        return
    try:
        with _connect(_db_path()) as conn:
            cursor = conn.execute(
                "UPDATE digests SET broadcast_id = ?, broadcast_status = ?, "
                "broadcast_recipients = COALESCE(?, broadcast_recipients) WHERE date = ?",
                (broadcast_id, status, recipients, date_str),
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


def broadcast_recipients(date_str: str) -> int:
    """Recipient count recorded for a date's broadcast (0 if unknown)."""
    try:
        with _connect(_db_path()) as conn:
            row = conn.execute("SELECT broadcast_recipients FROM digests WHERE date = ?", (date_str,)).fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.Error as e:
        logger.error("DB error reading broadcast_recipients for %s: %s", date_str, e)
        return 0


def delete_run(run_id: int):
    """Delete a run and all associated data across all tables."""
    try:
        with _connect(_db_path()) as conn:
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
