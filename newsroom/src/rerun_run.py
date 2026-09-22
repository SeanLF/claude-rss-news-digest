"""Re-run a run's curation span from its archived inputs, with model calls, N times.

The gate's speed-and-cost band (spec §7.4) needs the OLD system's same-day spread, and replay.py
makes no model calls. This restores every archived input of run N into a scratch input dir and
drives orchestrate.orchestrate_selections (CLUSTER..COHERENCE + repair) then merge.assemble_selections
over it with the run's date pinned. Fetch, fulltext, threads and render are outside this span.
Usage (in the container, see bin/rerun-run): rerun_run.py --run 300 --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import shutil
import time
from pathlib import Path

import config
import db
import merge
import orchestrate
import rerun_stage

WORK = Path("/app/data/rerun-run")

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


async def rerun(run: int, *, reps: int, work: Path) -> dict:
    archive = db.get_run_artifacts(run)
    if "sources.csv" not in archive or "weekly_recap.txt" not in archive:
        raise SystemExit(f"run {run} predates the closed archive (2026-09-18); pick run >= 300")
    today = rerun_stage._run_date(run)
    results: list[dict] = []
    for i in range(reps):
        # A directory this module created under WORK/run<N>/rep<i>; never a caller-supplied path.
        rep_dir = work / f"rep{i}"
        if rep_dir.exists():
            shutil.rmtree(rep_dir)
        results.append(await _one_rep(archive, rep_dir, today))
        print(f"  rep {i}: {results[-1]}", flush=True)
    summary = summarise(run, results)
    work.mkdir(parents=True, exist_ok=True)
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()
    if db.current_db_path() is None:
        db.init(config.DB_PATH, config.MIGRATIONS_DIR, apply_migrations=False)
    summary = asyncio.run(rerun(args.run, reps=max(1, args.reps), work=WORK / f"run{args.run}"))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
