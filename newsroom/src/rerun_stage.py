"""Re-run one curation stage from a run's archived inputs and compare the outputs.

Probe 0's re-run half (docs/2026-09-17-probe-0-stage-purity.md): the static half showed which
stages the archive is closed under; this measures how far a closed stage's output moves when
nothing but the model's sampling changes. Every rep is kept, and each is compared with the
archived output and with the other reps.

Usage (in the newsroom container, see bin/rerun-stage):
  rerun_stage.py --run 300 --stage recap --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import difflib
import json
import re
import sqlite3
from itertools import combinations
from pathlib import Path

import config
import db
import eval_coherence

AGENTS_DIR = Path("/app/.claude/agents")
WORK = Path("/app/data/rerun")

# (agent file, the file the stage writes)
STAGES = {
    "recap": ("recap.md", "recap.txt"),
    "select": ("select.md", "selected.json"),
    "coherence": ("coherence.md", "coherence_report.json"),
}

# The same filename grammar test_archive_closure.py holds the archive to.
_FILENAME = re.compile(r"\b[A-Za-z0-9_-]+\.(?:json|jsonl|csv|tsv|txt|md|ya?ml)\b")
_GLOBBED = re.compile(r"^articles_\d+\.csv$")


def stage_inputs(agent_body: str, archive: dict[str, str], *, output_name: str) -> dict[str, str]:
    """The archived files the stage's prompt names, minus the stage's own output, plus the
    article CSVs (archived by glob)."""
    named = set(_FILENAME.findall(agent_body)) - {output_name}
    return {n: c for n, c in archive.items() if n in named or _GLOBBED.match(n)}


def compare(archived: str, reps: list[str]) -> dict:
    """difflib similarity of each rep to the archived output and of every rep pair."""
    ratio = lambda a, b: round(difflib.SequenceMatcher(None, a, b).ratio(), 3)  # noqa: E731
    return {
        "vs_archived": [ratio(archived, r) for r in reps],
        "pairwise": [ratio(a, b) for a, b in combinations(reps, 2)],
    }


def _run_date(run: int) -> datetime.date | None:
    path = db.current_db_path()
    if path is None:
        return None
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT date(run_at) FROM digest_runs WHERE id=?", (run,)).fetchone()
    return datetime.date.fromisoformat(row[0]) if row and row[0] else None


async def rerun(run: int, stage: str, *, reps: int, work: Path, model_override: str | None = None) -> dict:
    agent_file, output_name = STAGES[stage]
    archive = db.get_run_artifacts(run)
    if output_name not in archive:
        raise SystemExit(f"run {run} has no archived {output_name}; nothing to compare against")
    work.mkdir(parents=True, exist_ok=True)
    body_for_inputs = (
        (AGENTS_DIR / agent_file).read_text(encoding="utf-8") if (AGENTS_DIR / agent_file).exists() else ""
    )
    model, body, thinking, tools = eval_coherence.load_agent_for_eval(
        AGENTS_DIR / agent_file, work, model_override, today=_run_date(run)
    )
    inputs = stage_inputs(body_for_inputs or body, archive, output_name=output_name)
    for name, content in inputs.items():
        (work / name).write_text(content, encoding="utf-8")

    outputs: list[str] = []
    stem, suffix = output_name.rsplit(".", 1)
    for i in range(reps):
        out = work / output_name
        await eval_coherence.run_agent_to_file(stage, out, model, body, thinking, tools)
        text = out.read_text(encoding="utf-8")
        (work / f"{stem}.{i}.{suffix}").write_text(text, encoding="utf-8")
        outputs.append(text)
        print(f"  rep {i}: {len(text)} chars")

    summary = {
        "run": run,
        "stage": stage,
        "model": model,
        "reps": reps,
        "inputs": sorted(inputs),
        **compare(archive[output_name], outputs),
    }
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--stage", choices=sorted(STAGES), required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default=None, help="override the agent's frontmatter model")
    args = ap.parse_args()

    if db.current_db_path() is None:
        db.init(config.DB_PATH, config.MIGRATIONS_DIR, apply_migrations=False)
    work = WORK / f"run{args.run}" / args.stage
    summary = asyncio.run(rerun(args.run, args.stage, reps=max(1, args.reps), work=work, model_override=args.model))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
