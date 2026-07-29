"""Backfill thread labels that were harvested from the wrong cluster.

A one-off repair, not part of the pipeline. Thread labels used to come from SELECT's
`cluster_index` -- a 0-based position into a several-hundred-element array that a model counts by
eye. It is wrong ~16% of the time, so 110 of 668 archived installments are published on
/thread/{id} under a label describing a different story from the SAME run. `b114c6a` fixed it
forward by deriving the label from the entry's `article_ids` instead; this applies that same
derivation to what already shipped.

Only installments that STARTED a thread are corrected. Where the linker CONTINUED a thread it
matched on the wrong label, so the installment sits in the wrong thread as well as under the
wrong name -- relabelling it alone would splice a visibly unrelated story into an existing arc
(thread 12's Russia-Ukraine record would read "...strikes -> China DUV chipmaking tools ->
strikes"). Those are reported and refused; detaching them is a different operation with a
different blast radius, and it needs a decision about where the installment goes.

The correction is POSITIONAL: the Nth installment of a run is the Nth entry of that run's
selected labels, because resolve_threads writes them in that order. That is PROVEN per run, not
assumed -- `_old_labels` replays the pre-b114c6a derivation and the run is skipped unless it
reproduces the stored labels exactly, in order. Equal counts are not enough: the old and new
derivations skip DIFFERENT entries (the old dropped an out-of-range cluster_index, the new drops
ids that map to no cluster), so two runs can agree on length while diverging at every position,
and a length-only guard would relabel by coincidence and exit 0.

Only a thread with EXACTLY ONE installment is corrected. "Started a thread" cannot be read off
`matched_score`: merge_thread moves installments between threads without touching it, so a NULL
score means "started some thread", possibly one that no longer exists. And relabelling the head
of a thread that was later continued splices the arc exactly as relabelling a continuation does
-- the following installments linked BECAUSE of the wrong label. A single-installment thread has
no arc to splice, which is the only case that is safe by construction.

Usage:
  bin/relabel-installments                # dry run, prints the plan
  bin/relabel-installments --apply        # commits, after snapshotting the DB

Published issues are NOT rewritten: `digests` holds immutable HTML blobs and stays exactly as
sent. This reconciles story identity on the archive and for the linker going forward.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

import threads
from config import DB_PATH
from repair_threads import snapshot

logger = logging.getLogger(__name__)


def _artifacts(conn: sqlite3.Connection) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for run, name, content in conn.execute(
        "SELECT run_id, artifact_name, content FROM run_artifacts "
        "WHERE artifact_name IN ('clusters.json', 'selected.json')"
    ):
        out.setdefault(run, {})[name] = content
    return out


def _old_labels(clusters_doc: dict, selected_doc: dict) -> list[str]:
    """Replay the pre-b114c6a derivation: label = clusters[entry["cluster_index"]].

    This is the oracle for alignment. If replaying it reproduces the stored labels in order, the
    Nth installment really is the Nth selected entry and the corrections can be trusted; if it
    does not, something has rewritten or reordered the rows since and the run must be left alone.
    """
    clusters = clusters_doc.get("clusters", [])
    out: list[str] = []
    for tier in ("must_know", "should_know"):
        for entry in selected_doc.get(tier, []) or []:
            idx = entry.get("cluster_index")
            if isinstance(idx, int) and 0 <= idx < len(clusters):
                out.append(clusters[idx].get("story", ""))
    return out


def plan(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Return (correctable, skipped). Read-only; nothing is written here."""
    arts = _artifacts(conn)
    thread_label = dict(conn.execute("SELECT id, label FROM threads").fetchall())
    # Installments per thread. merge_thread moves rows between threads without touching
    # matched_score, so the score cannot tell "started THIS thread" from "started one that was
    # since merged away" -- only the thread's actual size can.
    size = dict(conn.execute("SELECT thread_id, COUNT(*) FROM thread_installments GROUP BY thread_id").fetchall())

    runs = [r for (r,) in conn.execute("SELECT DISTINCT run_id FROM thread_installments ORDER BY run_id")]
    fix: list[dict] = []
    skipped: list[dict] = []

    for run in runs:
        a = arts.get(run, {})
        if "clusters.json" not in a or "selected.json" not in a:
            skipped.append({"run_id": run, "reason": "no_artifacts"})
            continue
        try:
            correct = threads.selected_labels(json.loads(a["clusters.json"]), json.loads(a["selected.json"]))
        except (ValueError, TypeError) as e:  # a corrupt artifact must not abort the whole backfill
            skipped.append({"run_id": run, "reason": "unreadable_artifacts", "detail": str(e)})
            continue

        rows = conn.execute(
            "SELECT id, thread_id, cluster_story, matched_score FROM thread_installments WHERE run_id = ? ORDER BY id",
            (run,),
        ).fetchall()
        if len(rows) != len(correct):
            skipped.append({"run_id": run, "reason": "unalignable_run", "detail": f"{len(rows)} vs {len(correct)}"})
            continue
        # Each stored label must be what the OLD derivation put at that position (not yet
        # corrected) or what the new one puts there (already corrected by a previous --apply).
        # Anything else means the rows have been rewritten or reordered by something other than
        # this tool, and the positional mapping cannot be trusted for this run.
        #
        # Accepting the corrected value is what keeps a second run meaningful: without it this
        # tool invalidates its own proof the moment it writes, and every later run reports
        # "0 to relabel" alongside a pile of skipped runs -- indistinguishable from "all done".
        old = _old_labels(json.loads(a["clusters.json"]), json.loads(a["selected.json"]))
        if any(
            stored not in (was, want["story"]) for (_, _, stored, _), was, want in zip(rows, old, correct, strict=True)
        ):
            skipped.append({"run_id": run, "reason": "alignment_unproven"})
            continue

        for (iid, tid, stored, score), want in zip(rows, correct, strict=True):
            if stored == want["story"]:
                continue
            entry = {
                "installment_id": iid,
                "thread_id": tid,
                "run_id": run,
                "stored": stored,
                "correct": want["story"],
            }
            if score is not None or size.get(tid, 0) != 1:
                # Safe only when the row started a thread AND that thread never went anywhere:
                # a continuation is in the wrong thread too (the linker matched on the wrong
                # label), and the head of a continued thread is just as bad, because the rows
                # after it linked BECAUSE of this label. Both conditions are required -- a
                # merged-away arc can leave a continuation sitting alone on a size-1 thread.
                reason = "continuation" if score is not None else "head_of_continued_thread"
                skipped.append({**entry, "reason": reason})
                continue
            # Only rewrite the thread's own label when it is still SHOWING this wrong one; a thread
            # that has since moved on carries a label this backfill has no opinion about.
            entry["also_thread_label"] = thread_label.get(tid) == stored
            fix.append(entry)

    return fix, skipped


def apply_fixes(conn: sqlite3.Connection, fix: list[dict]) -> int:
    """Write the corrections in one transaction. Returns the number of installments changed."""
    if not fix:
        return 0
    with conn:  # commits on success, rolls back on any error
        for row in fix:
            conn.execute(
                "UPDATE thread_installments SET cluster_story = ? WHERE id = ?",
                (row["correct"], row["installment_id"]),
            )
            if row.get("also_thread_label"):
                conn.execute(
                    "UPDATE threads SET label = ? WHERE id = ? AND label = ?",
                    (row["correct"], row["thread_id"], row["stored"]),
                )
    return len(fix)


def _print_plan(fix: list[dict], skipped: list[dict], *, applying: bool) -> None:
    for row in fix:
        flag = " +thread-label" if row.get("also_thread_label") else ""
        logger.info("run %s thread %s%s", row["run_id"], row["thread_id"], flag)
        logger.info("    was : %r", row["stored"])
        logger.info("    now : %r", row["correct"])

    refused = [s for s in skipped if s.get("reason") in ("continuation", "head_of_continued_thread")]
    if refused:
        logger.info("")
        logger.info("REFUSED -- %d wrong label(s) on threads that have an arc.", len(refused))
        logger.info("Relabelling any of these splices an unrelated story into a published arc:")
        logger.info("  continuation             = the linker matched on the wrong label, so the")
        logger.info("                             installment is in the wrong thread as well.")
        logger.info("  head_of_continued_thread = the rows after it linked BECAUSE of this label.")
        for s in refused:
            logger.info(
                "  run %s thread %s (%s): %r -> %r", s["run_id"], s["thread_id"], s["reason"], s["stored"], s["correct"]
            )

    other = [s for s in skipped if s.get("reason") not in ("continuation", "head_of_continued_thread")]
    for s in other:
        logger.info("skipped run %s (%s%s)", s["run_id"], s["reason"], f": {s['detail']}" if s.get("detail") else "")

    logger.info("")
    logger.info(
        "%d installment(s) to relabel, %d refused, %d run(s) skipped.%s",
        len(fix),
        len(refused),
        len(other),
        "" if applying else "  Dry run -- nothing written. Re-run with --apply.",
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="commit the corrections (default is a dry run)")
    p.add_argument("--db", default=str(DB_PATH), help="target DB (default: the configured digest.db)")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: database not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        fix, skipped = plan(conn)
        _print_plan(fix, skipped, applying=args.apply)
        if args.apply and fix:
            # Snapshot BEFORE the write: this edits rows the public archive renders.
            logger.info("snapshot: %s", snapshot(db_path))
            logger.info("relabelled %d installment(s)", apply_fixes(conn, fix))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
