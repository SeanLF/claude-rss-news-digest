"""Fold duplicate threads back into the threads they should have continued.

A one-off repair, not part of the pipeline. When the linker fails to recognise a
continuation it opens a SECOND thread for a story already being tracked, which
resets the reader-visible "Ongoing - day N" badge to day 1 and leaves the
duplicate holding the newest label -- the label the linker matches on next run,
so the split compounds. Run 244 (2026-07-25) split five stories this way when a
quoted-id type check discarded all 16 links; `0a86b45` stopped it recurring, but
the rows are still there.

`ThreadStore.merge_thread` does the work and is atomic and idempotent. This is
its caller: it resolves pairs, prints a plan, and only writes when told to.

Pairs are given EXPLICITLY. Nothing here infers which thread continues which --
that inference is exactly what failed, and re-running it to repair its own damage
would be a second outage. An operator reads the plan and decides.

    bin/repair-threads --merge 378:356 --merge 382:12          # dry run, prints the plan
    bin/repair-threads --merge 378:356 --merge 382:12 --apply  # commits
"""

import argparse
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import threads
from config import DB_PATH

logger = logging.getLogger(__name__)


def parse_pair(spec: str) -> tuple[int, int]:
    """Parse a `SOURCE:TARGET` pair. Raises ValueError on anything ambiguous.

    Strict on purpose: a mis-parsed id here deletes the wrong thread, and the
    whole batch is rejected before any of it is applied.
    """
    parts = spec.split(":")
    if len(parts) != 2 or not all(p.strip().isdecimal() for p in parts):
        raise ValueError(f"expected SOURCE:TARGET with two thread ids, got {spec!r}")
    source, target = (int(p) for p in parts)
    if source == target:
        raise ValueError(f"cannot merge thread {source} into itself")
    return source, target


def _row(conn: sqlite3.Connection, tid: int) -> tuple[str, int, int] | None:
    """(label, installment count, first_run_id) for a thread, or None if it is gone."""
    got = conn.execute(
        "SELECT t.label, (SELECT COUNT(*) FROM thread_installments i WHERE i.thread_id = t.id), "
        "t.first_run_id FROM threads t WHERE t.id = ?",
        (tid,),
    ).fetchone()
    return (got[0], got[1], got[2]) if got else None


def _merged_day_count(conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
    """Installments the target will have after the merge.

    merge_thread collapses to one row per run_id across the merged set, so a plain
    target+source sum over-predicts whenever the two share a run (or the target
    already holds a stray same-run pair -- there is no UNIQUE(thread_id, run_id)).
    The operator sanity-checks the plan against this number, so it has to match.
    """
    return conn.execute(
        "SELECT COUNT(DISTINCT run_id) FROM thread_installments WHERE thread_id IN (?, ?)",
        (source_id, target_id),
    ).fetchone()[0]


def _reject_overlapping_pairs(pairs: list[tuple[int, int]]) -> None:
    """Refuse a batch whose pairs interact. plan() validates against the PRE-batch
    state while apply_merges mutates between pairs, so any overlap makes the plan a
    lie. Chained (B->Z then A->B) is the dangerous one: the first commits, the
    second dies on a target that no longer exists, and re-running cannot fix it
    because the operator now needs A->Z instead. A repeated source is quieter but
    worse -- the second merge is skipped as "already merged" and the run exits 0.
    """
    sources = [s for s, _ in pairs]
    targets = {t for _, t in pairs}
    if both := sorted(set(sources) & targets):
        raise ValueError(
            f"thread(s) {both} appear as both a source and a target in this batch; "
            "merges would chain. Run them separately, oldest target first."
        )
    if dupes := sorted({s for s in sources if sources.count(s) > 1}):
        raise ValueError(f"thread(s) {dupes} given as a source more than once; each can merge only once.")


def plan(conn: sqlite3.Connection, pairs: list[tuple[int, int]], *, force: bool = False) -> list[dict]:
    """Describe what each merge would do, without writing anything.

    Raises rather than returning a partial plan: every pair is validated up front
    so a bad id in the middle of a batch cannot be discovered after earlier pairs
    have already been applied.
    """
    _reject_overlapping_pairs(pairs)
    rows = []
    for source_id, target_id in pairs:
        target = _row(conn, target_id)
        if target is None:
            raise ValueError(f"merge target thread {target_id} does not exist")
        target_label, target_count, target_first = target

        source = _row(conn, source_id)
        if source is None:
            rows.append(
                {
                    "status": "already merged",
                    "source_id": source_id,
                    "target_id": target_id,
                    "target_label": target_label,
                    "source_installments": 0,
                    "target_installments": target_count,
                    "target_installments_after": target_count,
                }
            )
            continue
        source_label, source_count, source_first = source

        # Reversed-pair guard, on ORIGIN rather than length. The duplicate is by
        # definition the thread that opened LATER. Comparing installment counts was
        # the obvious proxy and is wrong in the case this tool exists for: the
        # duplicate holds the newest label, so subsequent runs link to it, and a
        # repair run a week after the split finds the duplicate legitimately longer
        # than the thread it should have continued. first_run_id does not drift.
        if source_first is not None and target_first is not None and source_first < target_first and not force:
            raise ValueError(
                f"pair {source_id}:{target_id} looks reversed -- source thread {source_id} opened at "
                f"run {source_first}, BEFORE target {target_id} at run {target_first}, so the source is "
                f"the older thread and should be the target. Pass --force if you really mean this."
            )

        rows.append(
            {
                "status": "merge",
                "source_id": source_id,
                "source_label": source_label,
                "source_installments": source_count,
                "target_id": target_id,
                "target_label": target_label,
                "target_installments": target_count,
                "target_installments_after": _merged_day_count(conn, source_id, target_id),
            }
        )
    return rows


def apply_merges(conn: sqlite3.Connection, pairs: list[tuple[int, int]]) -> int:
    """Run the merges. Returns how many actually moved (already-merged pairs are skipped)."""
    store = threads.ThreadStore(conn)
    applied = 0
    for source_id, target_id in pairs:
        result = store.merge_thread(source_id, target_id)
        if result is None:
            logger.info("thread %d already merged; skipping", source_id)
            continue
        applied += 1
        logger.info(
            "merged thread %d into %d: %d installment(s) moved, %d question(s) moved, "
            "%d installment(s) dropped as same-run duplicates",
            source_id,
            target_id,
            result.get("installments_moved", 0),
            result.get("questions_moved", 0),
            result.get("installments_dropped", 0),
        )
    return applied


def snapshot(db_path: Path) -> Path:
    """Copy the database before the first write. VACUUM INTO needs no extra tooling
    on the server and makes every failure mode recoverable -- a typo'd target that
    happens to exist passes every guard, and merge_thread records nothing that could
    reconstruct what it deleted."""
    dest = db_path.with_name(f"{db_path.name}.pre-repair-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    # Explicit close, not `with sqlite3.connect(...)`: the context manager commits but
    # does NOT close, so the connection would keep its lock and the merge that follows
    # fails with "database is locked". isolation_level=None because VACUUM cannot run
    # inside a transaction.
    snap = sqlite3.connect(db_path, isolation_level=None)
    try:
        snap.execute("VACUUM INTO ?", (str(dest),))
    finally:
        snap.close()
    logger.info("snapshot written to %s", dest)
    return dest


def _print_plan(rows: list[dict], *, applying: bool) -> None:
    header = "APPLYING" if applying else "DRY RUN -- nothing will be written (pass --apply to commit)"
    print(f"\n{header}\n")
    for r in rows:
        if r["status"] == "already merged":
            print(f"  [skip] thread {r['source_id']} is already merged into {r['target_id']}")
            continue
        print(f"  merge {r['source_id']} -> {r['target_id']}")
        print(f"      from: {r['source_label']}  ({r['source_installments']} installment(s))")
        print(f"        to: {r['target_label']}  ({r['target_installments']} installment(s))")
        print(f"     after: day count becomes {r['target_installments_after']}")
    print()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--merge",
        action="append",
        default=[],
        metavar="SOURCE:TARGET",
        help="fold duplicate thread SOURCE into TARGET; repeatable",
    )
    p.add_argument("--apply", action="store_true", help="commit the merges (default is a dry run)")
    p.add_argument("--force", action="store_true", help="allow a pair whose source has more history than its target")
    p.add_argument("--db", default=str(DB_PATH), help="target DB (default: the configured digest.db)")
    args = p.parse_args(argv)

    if not args.merge:
        p.print_usage(sys.stderr)
        print("error: at least one --merge SOURCE:TARGET is required", file=sys.stderr)
        return 2

    try:
        pairs = [parse_pair(spec) for spec in args.merge]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: database not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    # Match db._connect: FK enforcement is off by default in SQLite, and this is the
    # one script whose job is deleting thread rows in production.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        try:
            rows = plan(conn, pairs, force=args.force)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        _print_plan(rows, applying=args.apply)
        if not args.apply:
            return 0
        snapshot(db_path)
        applied = apply_merges(conn, pairs)
        print(f"Merged {applied} thread(s).\n")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
