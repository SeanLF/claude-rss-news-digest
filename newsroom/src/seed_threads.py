"""Seed the thread tables by replaying production thread identity + synthesis over recent archived
runs, so enabling threads gives day-1 continuity instead of a multi-day blank ramp.

Runs the SAME production code as the live pipeline (`threads.resolve_threads` +
`thread_synthesis.synthesize_threads`) over the last N completed runs whose curation trace
(clusters + selected + articles) is archived in `run_artifacts`, IN ORDER, writing real
threads/installments to the target DB. The replay is PACED: a burst of linker + synthesis calls
otherwise exhausts the Claude OAuth token's per-minute rate limit, and the linker then fails open
to all-NEW -- silently producing a history-less table that looks seeded but threads nothing.

  # local (needs CLAUDE_CODE_OAUTH_TOKEN in the environment)
  docker compose run --rm -e CLAUDE_CODE_OAUTH_TOKEN digest-newsroom \
    .venv/bin/python src/seed_threads.py --reset

  # production -- on the server, AFTER deploy, while THREADS_ENABLED is still false:
  docker run --rm --env-file /opt/news-digest/.env -v news-digest-data:/app/data \
    <registry>/digest-newsroom:latest .venv/bin/python src/seed_threads.py --reset

Seed first, eyeball, THEN flip THREADS_ENABLED -- the seed runs invisibly while the flag is off,
so a re-seed (`--reset`) before going live is free of reader impact.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sqlite3
import time
from pathlib import Path

import db
import thread_synthesis
import threads
from config import (
    DB_PATH,
    MIGRATIONS_DIR,
    THREAD_DORMANT_AFTER,
    THREAD_LATEBIND_MAX_EXTRA,
    THREAD_LATEBIND_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Delete order respects no FKs (SQLite FKs are off by default here) but reads clearly child->parent.
THREAD_TABLES = ("thread_questions", "thread_installments", "thread_runs", "threads")


def _run_artifacts(conn: sqlite3.Connection, run_id: int) -> dict[str, str]:
    return dict(conn.execute("SELECT artifact_name, content FROM run_artifacts WHERE run_id = ?", (run_id,)).fetchall())


def _articles_from_artifacts(artifacts: dict[str, str]) -> dict:
    """id -> {title, summary} from the archived articles_*.csv trace (what Claude curated from) --
    the same shape `run._load_run_articles` builds for the live synthesis."""
    arts: dict = {}
    for name, content in artifacts.items():
        if not (name.startswith("articles_") and name.endswith(".csv")):
            continue
        for row in csv.DictReader(io.StringIO(content)):
            aid = row.get("article_id")
            if aid:
                arts[aid] = {"title": row.get("title", ""), "summary": row.get("summary", "")}
    return arts


def seedable_runs(
    conn: sqlite3.Connection, *, runs_back: int, first: int | None = None, last: int | None = None
) -> list[int]:
    """Completed runs whose curation trace (clusters.json + selected.json) is archived, oldest->
    newest. An explicit [first, last] window if given, else the most recent `runs_back`."""
    rows = conn.execute(
        """
        SELECT r.id FROM digest_runs r
        WHERE r.completed_at IS NOT NULL
          AND EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = r.id AND a.artifact_name = 'clusters.json')
          AND EXISTS (SELECT 1 FROM run_artifacts s WHERE s.run_id = r.id AND s.artifact_name = 'selected.json')
        ORDER BY r.id
        """
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return []
    if first is not None or last is not None:
        lo = first if first is not None else ids[0]
        hi = last if last is not None else ids[-1]
        return [i for i in ids if lo <= i <= hi]
    return ids[-runs_back:] if runs_back > 0 else ids


def thread_tables_populated(conn: sqlite3.Connection) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in THREAD_TABLES)


def reset_thread_tables(conn: sqlite3.Connection) -> None:
    for t in THREAD_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def seed(conn: sqlite3.Connection, run_ids: list[int], *, latebind: bool = False, pace: float = 5.0) -> None:
    """Replay identity + synthesis over `run_ids` (assumed oldest->newest), paced between runs."""
    store = threads.ThreadStore(conn)
    for i, rid in enumerate(run_ids):
        artifacts = _run_artifacts(conn, rid)
        stories = threads.selected_labels(
            json.loads(artifacts["clusters.json"]), json.loads(artifacts["selected.json"])
        )
        assignments = threads.resolve_threads(stories, rid, store, dormant_after=THREAD_DORMANT_AFTER)
        installments, audit_failures = thread_synthesis.synthesize_threads(
            assignments,
            _articles_from_artifacts(artifacts),
            rid,
            store,
            latebind_threshold=THREAD_LATEBIND_THRESHOLD if latebind else None,
            latebind_max_extra=THREAD_LATEBIND_MAX_EXTRA,
        )
        continued = sum(1 for a in assignments if not a.is_new)
        logger.info(
            "run %d: %d stories -> %d continued, %d new; %d synthesized (%d audit fail-open)",
            rid,
            len(assignments),
            continued,
            len(assignments) - continued,
            len(installments),
            audit_failures,
        )
        if pace and i < len(run_ids) - 1:  # no need to wait after the last run
            time.sleep(pace)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH), help="target DB (default: the configured digest.db)")
    p.add_argument("--runs-back", type=int, default=12, help="seed the most recent N archived runs (default 12)")
    p.add_argument("--from", dest="first", type=int, help="explicit first run id (with --to; overrides --runs-back)")
    p.add_argument("--to", dest="last", type=int, help="explicit last run id")
    p.add_argument("--latebind", action="store_true", help="widen synthesis input (match prod THREAD_LATEBIND)")
    p.add_argument("--pace", type=float, default=5.0, help="seconds between runs -- rate-limit guard (default 5)")
    p.add_argument("--reset", action="store_true", help="clear existing thread tables first (required to re-seed)")
    p.add_argument("--dry-run", action="store_true", help="print the run range and exit (no LLM calls, no writes)")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    try:
        # Run selection is read-only (digest_runs + run_artifacts exist on any real DB), so --dry-run
        # never writes -- the schema migration happens only on the actual seed path below.
        run_ids = seedable_runs(conn, runs_back=args.runs_back, first=args.first, last=args.last)
        if not run_ids:
            logger.error("No archived runs with clusters.json + selected.json found -- nothing to seed.")
            return 1
        logger.info(
            "Seeding threads from runs %d-%d (%d runs)%s",
            run_ids[0],
            run_ids[-1],
            len(run_ids),
            ", late-bind ON" if args.latebind else "",
        )
        if args.dry_run:
            return 0
        conn.close()
        db.init(db_path, MIGRATIONS_DIR)  # ensure the thread schema exists (idempotent migrations)
        conn = sqlite3.connect(db_path)  # reopen so a freshly-created schema is visible
        if thread_tables_populated(conn):
            if not args.reset:
                logger.error("Thread tables already populated; pass --reset to clear and re-seed.")
                return 1
            reset_thread_tables(conn)
        seed(conn, run_ids, latebind=args.latebind, pace=args.pace)
        total = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        multi = conn.execute(
            "SELECT COUNT(*) FROM (SELECT thread_id FROM thread_installments GROUP BY thread_id HAVING COUNT(*) >= 2)"
        ).fetchone()[0]
        logger.info("Done: %d threads (%d multi-day) seeded.", total, multi)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
