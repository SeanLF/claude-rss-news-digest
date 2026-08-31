"""Shared plumbing for the stage thinking/model sweeps.

Everything here runs INSIDE the digest-newsroom container (see run.sh). It never
imports or mutates the live pipeline's behaviour -- it only *reads* the prod
modules (claude_cli, repair, eval_stages, ...) and the live `.claude/agents/*.md`
prompts, so a sweep measures whatever prod currently says.

Two things this module exists to guarantee:

1. Every arm sees BYTE-IDENTICAL input. Each replay dir is rebuilt from the same
   archived `run_artifacts` rows before every call, so a stage that rewrites its
   own input cannot leak across arms.
2. The manipulation is PROVEN, not assumed. `record()` writes the full raw SDK
   usage dict, and `thinking_tokens()` pulls out the number that shows whether
   extended thinking actually engaged. A previous sweep in this repo was
   invalidated because every arm silently ran disabled; that is checked here.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

import claude_cli  # noqa: E402

DB = Path("/app/data/digest.db")
OUT = Path("/app/sweep/out")
PROD_INPUT_MARKER = "/app/data/claude_input/"


# --------------------------------------------------------------------------- #
# Agent specs (mirrors orchestrate.parse_agent_spec / eval_coherence.load_agent).
# --------------------------------------------------------------------------- #
def load_agent(path: Path) -> tuple[str, str, dict, list[str]]:
    """(model, body, thinking, tools) from an agent .md, applying the same default
    as production: orchestrate._THINKING (disabled) unless the agent opts in."""
    text = path.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        k, _, v = line.strip().partition(":")
        if k.strip():
            fields[k.strip()] = v.strip().strip("\"'")
    tools = fields.get("tools", "Read, Write").replace(",", " ").split()
    tv = fields.get("thinking")
    return fields["model"], body.strip(), ({"type": tv} if tv else {"type": "disabled"}), tools


def redirect_body(body: str, target: Path) -> str:
    """Point an agent prompt's hardcoded prod input dir at a replay dir. Fails loud
    if the marker is gone (prompt paths drifted) rather than running against /app/data."""
    if PROD_INPUT_MARKER not in body:
        raise SystemExit(f"prompt has no {PROD_INPUT_MARKER!r} to redirect; paths drifted")
    return body.replace(PROD_INPUT_MARKER, f"{target}/")


# --------------------------------------------------------------------------- #
# Archived-run replay.
# --------------------------------------------------------------------------- #
def artifacts(run_id: int) -> dict[str, str]:
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT artifact_name, content FROM run_artifacts WHERE run_id = ?", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise SystemExit(f"run {run_id}: no archived artifacts")
    return dict(rows)


def write_sources_csv(dest: Path) -> None:
    """Regenerate sources.csv from the live sources.json exactly as prepare.py does
    (rather than copying a stale data/claude_input one)."""
    raw = json.loads(Path("/app/sources.json").read_text(encoding="utf-8"))
    sources = raw if isinstance(raw, list) else raw["sources"]
    with open(dest, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "bias", "factuality", "perspective"])
        for s in sources:
            w.writerow([s["id"], s["name"], s["bias"], s["factuality"], s.get("perspective", "")])


def build_replay(run_id: int, dest: Path, names: list[str], *, sources_csv: bool = True) -> dict[str, str]:
    """Rebuild `dest` from run_id's archived artifacts. Destructive by design: the
    dir is wiped first so no stage output from a previous arm can survive into the
    next one and be mistaken for this arm's work."""
    import shutil

    arts = artifacts(run_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for n in names:
        if n.endswith(".csv") and n not in arts:
            continue  # articles_4/5.csv are not present on every run
        if n not in arts:
            raise SystemExit(f"run {run_id}: missing required artifact {n}")
        (dest / n).write_text(arts[n], encoding="utf-8")
    if sources_csv:
        write_sources_csv(dest / "sources.csv")
    return arts


def article_csv_names(run_id: int) -> list[str]:
    return sorted(n for n in artifacts(run_id) if n.startswith("articles_") and n.endswith(".csv"))


# --------------------------------------------------------------------------- #
# Running an arm + proving the manipulation took effect.
# --------------------------------------------------------------------------- #
def thinking_tokens(usage: dict) -> int:
    """Thinking tokens the API actually billed. 0 on a disabled arm is the expected
    negative control; 0 on an ADAPTIVE arm means the model chose not to think (a
    real finding), not that the flag failed -- adaptive is the model's call."""
    d = usage.get("output_tokens_details")
    return int(d.get("thinking_tokens", 0)) if isinstance(d, dict) else 0


async def run_arm(
    *, model: str, body: str, thinking: dict, tools: list[str], out_path: Path | None,
    idle_timeout: float = 300.0, prompt: str = "Begin.", max_turns: int | None = None,
) -> tuple[claude_cli.StageResult, float]:
    """One arm call through the production SDK path. Returns (result, wall_seconds).

    `out_path` (when given) is deleted first and REQUIRED to exist afterwards, so a
    stage that silently wrote nothing fails here instead of being scored against a
    previous arm's leftover file."""
    if out_path is not None and out_path.exists():
        out_path.unlink()
    t0 = time.monotonic()
    res = await claude_cli.run_agent(
        prompt,
        model=model,
        system_prompt=body,
        permission_mode="acceptEdits" if tools else None,
        allowed_tools=" ".join(tools) if tools else None,
        tools=tools,
        cwd="/app",
        idle_timeout=idle_timeout,
        thinking=thinking,
        max_turns=max_turns,
    )
    wall = time.monotonic() - t0
    if not res.ok:
        raise RuntimeError(f"arm failed: {res.error_summary()}")
    if out_path is not None and not out_path.exists():
        raise RuntimeError(f"arm wrote no {out_path.name}")
    return res, wall


def record(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def usage_row(res: claude_cli.StageResult, wall: float) -> dict:
    """The cost/latency/manipulation-proof block every sweep record carries. The FULL
    raw usage dict is kept so a later question about a field we didn't think to
    summarise can be answered without re-running (and re-paying for) the sweep."""
    return {
        "cost_usd": res.total_cost_usd,
        "duration_ms": res.duration_ms,
        "wall_s": round(wall, 2),
        "input_tokens": res.usage.get("input_tokens", 0),
        "output_tokens": res.usage.get("output_tokens", 0),
        "cache_read_tokens": res.usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": res.usage.get("cache_creation_input_tokens", 0),
        "thinking_tokens": thinking_tokens(res.usage),
        "files_read": list(res.files_read),
        "raw_usage": res.usage,
    }


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #
def spread(vals: list[float]) -> str:
    """mean / sd / min-max for a within-arm spread line. This is printed BEFORE any
    between-arm claim: this project has repeatedly found effects smaller than
    control noise."""
    if not vals:
        return "n=0"
    if len(vals) == 1:
        return f"n=1 {vals[0]:.3f}"
    return (
        f"n={len(vals)} mean={statistics.mean(vals):.3f} sd={statistics.stdev(vals):.3f} "
        f"[{min(vals):.3f}..{max(vals):.3f}]"
    )


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0
