"""Replay a finished run's TAIL from its archived artifacts: no model calls, no writes.

The pipeline's expensive half (cluster, select, write, coherence) is already archived per run in
``run_artifacts``. Its cheap half -- resolve ids, attach thread context, render web + email, judge
the invariants -- had no local entry point, so a change to it could only be verified by waiting
for the next scheduled run. Replay is that entry point.

Two constraints hold it honest:

* **Read-only.** A surface that records a run while verifying it corrupts what it measures. This
  module's own connection is opened ``mode=ro``, and it never starts a run. The one access it does
  not own is ``digest.attach_thread_context``, which opens its own read-write connection and today
  only reads through it -- ``test_replay_does_not_write_to_the_database`` compares table CONTENT
  either side of a replay so that stops being true loudly rather than silently.
* **Loud when it cannot replay.** A run with nothing archived raises rather than rendering an
  empty digest, because "no findings" and "nothing to look at" must not print the same.

Thread assignments are the awkward part. ``thread_assignments.json`` is written AFTER
``archive_run_artifacts`` sweeps ``claude_input/``, so runs before that was fixed never carried
it. For those, the assignments are reconstructed from the archived ``thread_links.json`` trace --
and only for stories the linker actually continued, because a refused proposal created a new
thread whose id the trace never recorded.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import config
import db
import digest
import render
import render_email
import run_health

logger = logging.getLogger(__name__)

ASSIGNMENTS_ARTIFACT = "thread_assignments.json"
LINKS_ARTIFACT = "thread_links.json"
SELECTIONS_ARTIFACT = "selections.json"
COHERENCE_ARTIFACT = "coherence_report.json"
INDEX_ARTIFACT = "article_index.json"

_THREAD_HREF = re.compile(r'href="([^"]*/thread/\d+)"')


@dataclass
class ReplayReport:
    """What a replay found. Printed by ``bin/replay``; asserted on by the tests."""

    run_id: int
    artifacts: list[str]
    assignments_source: str | None
    web_path: Path
    email_path: Path
    web_thread_links: list[str]
    email_thread_links: list[str]
    badges: int
    violations: list[str]
    coherence_kinds: dict[str, int] | None


def materialize(run_id: int, dest: Path) -> list[str]:
    """Write every artifact archived for ``run_id`` into ``dest``; return their names."""
    dest.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT artifact_name, content FROM run_artifacts WHERE run_id = ? ORDER BY artifact_name",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise LookupError(f"run {run_id} has no archived artifacts -- nothing to replay")

    names = []
    for name, content in rows:
        (dest / name).write_text(content or "")
        names.append(name)
    return names


def assignments_from_trace(trace: object) -> list[dict]:
    """Reconstruct thread assignments from an archived ``thread_links.json``.

    Only ``outcome == "continued"`` survives. A refused proposal created a NEW thread whose id the
    trace never records, so carrying ``proposed_thread`` through would staple one story's history
    onto another -- worse than showing no badge at all.
    """
    stories = trace.get("stories") if isinstance(trace, dict) else None
    if not isinstance(stories, list):
        return []

    out = []
    for story in stories:
        if not isinstance(story, dict) or story.get("outcome") != "continued":
            continue
        thread_id, label = story.get("proposed_thread"), story.get("label")
        if isinstance(thread_id, int) and isinstance(label, str):
            out.append({"story": label, "thread_id": thread_id, "is_new": False})
    return out


def _unarchived(dest: Path) -> str | None:
    """Provenance for an assignments file that is in ``dest`` but was never archived.

    Left by an earlier replay, or put there by hand to test a hypothesis. It is still USED -- that
    is what someone hand-placing it wants -- but it is never reported as archived evidence, and it
    is never deleted.
    """
    if not (dest / ASSIGNMENTS_ARTIFACT).exists():
        return None
    logger.warning(
        "%s in %s was not archived for this run; using it, but its provenance is unverified",
        ASSIGNMENTS_ARTIFACT,
        dest,
    )
    return f"{ASSIGNMENTS_ARTIFACT} (unarchived, already in {dest.name})"


def _resolve_assignments(dest: Path, archived: list[str]) -> str | None:
    """Ensure ``dest`` holds a thread_assignments.json, and say TRUTHFULLY where it came from.

    Provenance is decided by what the run actually archived, never by what happens to be sitting
    in ``dest``. Re-running into the same directory is normal (the CLI defaults to a stable
    data/replay/runN), so a previous pass's derived file is often present -- reading that as
    archived evidence would hide that the run predates the archival fix. Nothing here deletes:
    ``dest`` may be a directory someone chose with --out and put their own files in.
    """
    if ASSIGNMENTS_ARTIFACT in archived:
        return ASSIGNMENTS_ARTIFACT

    links = dest / LINKS_ARTIFACT
    if not links.exists():
        return _unarchived(dest)

    try:
        derived = assignments_from_trace(json.loads(links.read_text()))
    except ValueError:
        logger.warning("%s is not readable JSON; replaying without thread context", LINKS_ARTIFACT)
        return _unarchived(dest)
    if not derived:
        return _unarchived(dest)

    (dest / ASSIGNMENTS_ARTIFACT).write_text(json.dumps(derived, indent=2))
    return LINKS_ARTIFACT


def replay(run_id: int, dest: Path) -> ReplayReport:
    """Materialise ``run_id``'s artifacts into ``dest`` and re-render from them."""
    # From the CLI nobody has called db.init, so db has no path and get_run_health comes back
    # empty -- which the invariants then report as MALFORMED_HEALTH, i.e. "this run is broken"
    # when the truth is "I never opened the database". apply_migrations=False keeps this read-only:
    # migrating the database you are only inspecting is a write, and on a cloned prod DB a
    # surprising one.
    if db.current_db_path() is None:
        db.init(config.DB_PATH, config.MIGRATIONS_DIR, apply_migrations=False)

    artifacts = materialize(run_id, dest)

    selections_path = dest / SELECTIONS_ARTIFACT
    if not selections_path.exists():
        raise LookupError(f"run {run_id} archived no {SELECTIONS_ARTIFACT} -- the tail cannot be replayed")
    selections = json.loads(selections_path.read_text())

    assignments_source = _resolve_assignments(dest, artifacts)
    if assignments_source is None:
        logger.info("No thread assignments for run %d; rendering without thread context", run_id)

    # Only when the index was archived: selections.json from a resolved run already carries its
    # sources, and re-resolving without the index would blank them.
    if (dest / INDEX_ARTIFACT).exists():
        selections = digest.resolve_article_ids(selections, claude_input_dir=dest)

    with db.borrowed_run_id(run_id):
        selections = digest.attach_thread_context(selections, claude_input_dir=dest)

    web_path, email_path = dest / "replay-digest.html", dest / "replay-email.html"

    web_path.write_text(render.render_digest(selections, config.TEMPLATE_FILE))
    # Through replace_placeholders, not just render_digest: the template stage still carries
    # {{DATE}}/{{ISSUE_LABEL}} and no stylesheet, so an unsubstituted render invites bug reports
    # about styling that is simply not applied yet. It rewrites the file in place.
    render.replace_placeholders(web_path, selections, config.STYLES_FILE, render.extract_preheader(selections))
    web_html = web_path.read_text()

    email_html = render_email.render_email(selections)
    email_path.write_text(email_html)

    coherence = dest / COHERENCE_ARTIFACT
    return ReplayReport(
        run_id=run_id,
        artifacts=artifacts,
        assignments_source=assignments_source,
        web_path=web_path,
        email_path=email_path,
        web_thread_links=_THREAD_HREF.findall(web_html),
        email_thread_links=_THREAD_HREF.findall(email_html),
        badges=email_html.count("Ongoing"),
        violations=run_health.violations(db.get_run_health(run_id)),
        coherence_kinds=run_health.coherence_kind_counts(coherence.read_text() if coherence.exists() else None),
    )


def format_report(report: ReplayReport) -> str:
    """The CLI's output. Names what was NOT replayable as loudly as what was."""
    lines = [
        f"run {report.run_id}: replayed {len(report.artifacts)} archived artifact(s)",
        f"  thread assignments : {report.assignments_source or 'NONE -- no thread context in this render'}",
        f"  thread links (web) : {len(report.web_thread_links)} {report.web_thread_links[:3] or ''}",
        f"  thread links (mail): {len(report.email_thread_links)} {report.email_thread_links[:3] or ''}",
        f"  'Ongoing' badges   : {report.badges}",
        f"  coherence kinds    : {report.coherence_kinds if report.coherence_kinds is not None else 'no report archived'}",
        f"  run-health         : {', '.join(report.violations) if report.violations else 'no violations'}",
        f"  rendered           : {report.web_path}",
        f"                       {report.email_path}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_id", type=int, help="the finished run to replay")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to materialise artifacts and renders (default: data/replay/run<ID>)",
    )
    args = ap.parse_args()

    dest = args.out or (config.DATA_DIR / "replay" / f"run{args.run_id}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not config.THREADS_ENABLED:
        # Silence here would read as "this run had no threads", which is a different finding.
        print("warning: THREADS_ENABLED is off, so no thread context will render", file=sys.stderr)

    try:
        print(format_report(replay(args.run_id, dest)))
    except LookupError as e:
        print(f"cannot replay: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
