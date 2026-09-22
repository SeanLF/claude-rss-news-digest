"""Re-run a run's curation span from its archived inputs, with model calls, N times.

The gate's speed-and-cost band (spec §7.4) needs the OLD system's same-day spread, and replay.py
makes no model calls. Every agent prompt names /app/data/claude_input/ by absolute path (and
the WRITE fan-out and the repair re-check redirect that marker themselves), so a rep must own
that directory: bin/rerun-run runs one container per rep with a fresh host directory mounted
there, and this module drives orchestrate.orchestrate_selections (CLUSTER..COHERENCE + repair)
then merge.assemble_selections over it with the run's date pinned. Fetch, fulltext, threads and
render are outside this span.

Usage (in the container, see bin/rerun-run):
  rerun_run.py rep --run 300 --input-dir /app/data/claude_input
  rerun_run.py summarise --run 300 --dir /app/data/rerun-run/run300/<stamp>
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import time
from pathlib import Path

import config
import db
import merge
import orchestrate
import rerun_stage

REP_RESULT_NAME = "rep_result.json"

# Stage outputs the archive also holds; a re-run must produce these, never read them.
STAGE_OUTPUTS = frozenset(
    {
        "clusters.json",
        "cluster_tags.json",
        "cluster_health.json",
        "recap.txt",
        "selected.json",
        "article_fulltext.json",
        "fulltext_health.json",
        "cluster_cohesion.json",
        "draft_selections.json",
        "write_branches.json",
        "preheader.txt",
        "coherence_report.json",
        "repaired_fields.json",
        "recheck_draft.json",
        "recheck_report.json",
        "repair_resolution.json",
        "repair_health.json",
        "selections.json",
        "thread_assignments.json",
        "thread_links.json",
        "models.json",
    }
)


def restore_inputs(archive: dict[str, str], into: Path) -> list[str]:
    into.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in archive.items():
        if name in STAGE_OUTPUTS or name.startswith("thinking_"):
            continue
        (into / name).write_text(content, encoding="utf-8")
        written.append(name)
    return written


def summarise(run: int, reps: list[dict]) -> dict:
    return {
        "run": run,
        "reps": len(reps),
        "cost_usd": [r["cost_usd"] for r in reps],
        "wall_s": [r["wall_s"] for r in reps],
        "stories": [r["stories"] for r in reps],
    }


async def _one_rep(archive: dict[str, str], work: Path, today: datetime.date | None) -> dict:
    restore_inputs(archive, work)
    rows: list[dict] = []
    t0 = time.monotonic()
    await orchestrate.orchestrate_selections(claude_input_dir=work, on_usage=rows.append, today=today)
    selections_path = merge.assemble_selections(work)
    wall = time.monotonic() - t0
    selections = json.loads(selections_path.read_text(encoding="utf-8"))
    stories = len(selections.get("must_know", [])) + len(selections.get("should_know", []))
    cost = round(sum(float(r.get("api_cost_usd") or 0.0) for r in rows), 4)
    return {"cost_usd": cost, "wall_s": round(wall, 1), "stories": stories}


def _archive_for(run: int) -> dict[str, str]:
    archive = db.get_run_artifacts(run)
    if "sources.csv" not in archive or "weekly_recap.txt" not in archive:
        raise SystemExit(f"run {run} predates the closed archive (2026-09-18); pick run >= 300")
    return archive


async def rep(run: int, *, input_dir: Path) -> dict:
    """One rep into ``input_dir`` (mounted at /app/data/claude_input by bin/rerun-run); the
    result lands beside the outputs as rep_result.json."""
    if any(input_dir.iterdir()) if input_dir.exists() else False:
        raise SystemExit(f"{input_dir} is not empty; bin/rerun-run mounts a fresh directory per rep")
    result = await _one_rep(_archive_for(run), input_dir, rerun_stage._run_date(run))
    (input_dir / REP_RESULT_NAME).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def summarise_dir(run: int, band_dir: Path) -> dict:
    """Collect every rep*/rep_result.json under ``band_dir`` into summary.json."""
    results = [
        json.loads((d / REP_RESULT_NAME).read_text(encoding="utf-8"))
        for d in sorted(band_dir.glob("rep*"))
        if (d / REP_RESULT_NAME).exists()
    ]
    summary = summarise(run, results)
    (band_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_rep = sub.add_parser("rep")
    p_rep.add_argument("--run", type=int, required=True)
    p_rep.add_argument("--input-dir", type=Path, default=config.CLAUDE_INPUT_DIR)
    p_sum = sub.add_parser("summarise")
    p_sum.add_argument("--run", type=int, required=True)
    p_sum.add_argument("--dir", type=Path, required=True)
    args = ap.parse_args()
    if db.current_db_path() is None:
        db.init(config.DB_PATH, config.MIGRATIONS_DIR, apply_migrations=False)
    if args.cmd == "rep":
        out = asyncio.run(rep(args.run, input_dir=args.input_dir))
    else:
        out = summarise_dir(args.run, args.dir)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
