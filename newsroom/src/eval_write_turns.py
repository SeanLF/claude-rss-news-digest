"""Replay a run's WRITE branches through two deliveries, N reps each: the tool loop
production runs today (arm T) and a single inline turn with no tools (arm S).

Task 2 of docs/2026-09-03-stage-invocation-rewrite-plan.md. The prompt is the same in both
arms except for its I/O section (write_fanout.single_turn_branch_body is derived from the
branch prompt and a test holds the rules byte for byte), so a difference between arms is a
delivery difference, not a prompt difference.

**The primary output is the WITHIN-ARM SPREAD, not the between-arm difference.** Read the
noise floor first: on this endpoint (COHERENCE flags over 17 stories) one arm run five times
can differ from itself by more than any effect a prompt change has ever shown here. If the
arms differ by less than either arm's spread, there is no finding.

Endpoints per rep: stories written, COHERENCE flags (the shipped multi-turn checker run over
the assembled draft against the run's full corpus), L1 grader failures, and tokens. Arms run
sequentially so the container's cache never favours the second.

Makes REAL model calls on the subscription -- opt-in, never in CI.
Usage: bin/eval-write-turns [--run 285] [--reps 5] [--arms TS]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import statistics as stats
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app/src")

import claude_cli
import eval_coherence
import eval_graders
import orchestrate
import write_fanout

DB = Path("/app/data/digest.db")
WORK = Path("/app/data/eval-write-turns")
WRITE_AGENT = Path("/app/.claude/agents/write.md")
COHERENCE_AGENT = Path("/app/.claude/agents/coherence.md")
# What build_branches and the shipped COHERENCE need from the archive. weekly_recap.txt is not
# archived, so both arms run without it; the comparison is unaffected.
INPUT_NAMES = ("selected.json", "clusters.json", "article_fulltext.json", "recap.txt", "recent_digest_headlines.txt")
CONCURRENCY = 4


def restore_inputs(conn: sqlite3.Connection, run: int) -> Path:
    rows = conn.execute("SELECT artifact_name, content FROM run_artifacts WHERE run_id=?", (run,)).fetchall()
    keep = {n: c for n, c in rows if n in INPUT_NAMES or (n.startswith("articles_") and n.endswith(".csv"))}
    for required in ("selected.json", "clusters.json"):
        if required not in keep:
            raise SystemExit(f"run {run} has no archived {required} -- nothing to replay")
    root = WORK / str(run)
    if root.exists():
        shutil.rmtree(root)
    inputs = root / "input"
    inputs.mkdir(parents=True)
    for name, content in keep.items():
        (inputs / name).write_text(content, encoding="utf-8")
    return inputs


def _usage(res: claude_cli.StageResult) -> dict[str, float]:
    u = res.usage or {}
    return {
        "input": u.get("input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "cost": res.total_cost_usd or 0.0,
    }


async def write_branch(
    arm: str, branch: write_fanout.Branch, body: str, model: str, thinking: dict
) -> dict[str, float]:
    """One branch, one delivery. Leaves draft_selections.json in the branch dir either way."""
    draft_path = branch.dir / write_fanout.BRANCH_DRAFT_NAME
    draft_path.unlink(missing_ok=True)
    branch_prompt = write_fanout.branch_body(body, branch.dir)
    if arm == "T":
        res = await claude_cli.run_agent(
            "Begin.",
            model=model,
            system_prompt=branch_prompt,
            permission_mode="acceptEdits",
            allowed_tools="Read Write",
            tools=["Read", "Write"],
            cwd="/app",
            idle_timeout=300.0,
            thinking=thinking,
        )
        if not res.ok:
            raise RuntimeError(f"[T {branch.name}] {res.error_summary()}")
    else:
        res = await claude_cli.run_agent(
            write_fanout.branch_corpus(branch.dir),
            model=model,
            system_prompt=write_fanout.single_turn_branch_body(branch_prompt),
            permission_mode="acceptEdits",
            allowed_tools="",
            tools=[],
            cwd="/app",
            idle_timeout=300.0,
            thinking=thinking,
            max_turns=1,
        )
        if not res.ok:
            raise RuntimeError(f"[S {branch.name}] {res.error_summary()}")
        # A generic fenced-JSON extractor despite its name; the same one the single-turn
        # COHERENCE arm uses.
        payload = orchestrate.parse_coherence_report(res.text)
        if payload is None:
            raise RuntimeError(f"[S {branch.name}] reply held no JSON object")
        draft_path.write_text(json.dumps(payload), encoding="utf-8")
    write_fanout.branch_story(branch.dir)  # raises on a malformed draft, like production
    return _usage(res)


async def write_rep(
    arm: str, branches: list[write_fanout.Branch], body: str, model: str, thinking: dict
) -> tuple[dict, dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    usages: list[dict[str, float]] = []
    failures: list[str] = []

    async def one(branch: write_fanout.Branch) -> None:
        async with sem:
            try:
                usages.append(await write_branch(arm, branch, body, model, thinking))
            except Exception as e:
                failures.append(f"{branch.name}: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(b) for b in branches))
    ok = [
        b
        for b in branches
        if (b.dir / write_fanout.BRANCH_DRAFT_NAME).exists() and not any(f.startswith(b.name) for f in failures)
    ]
    draft = write_fanout.assemble_draft(ok)
    totals = {k: sum(u[k] for u in usages) for k in ("input", "cache_write", "cache_read", "output", "cost")}
    totals["branches_failed"] = len(failures)
    totals["failures"] = failures
    return draft, totals


def check_rep(inputs: Path, tag: str, draft: dict) -> dict:
    """The shipped multi-turn COHERENCE over one assembled draft, against the full corpus."""
    coh_dir = inputs.parent / f"coherence_{tag}"
    if coh_dir.exists():
        shutil.rmtree(coh_dir)
    coh_dir.mkdir()
    for path in inputs.iterdir():
        if path.name.startswith("articles_") or path.name == "article_fulltext.json":
            shutil.copy2(path, coh_dir / path.name)
    (coh_dir / "draft_selections.json").write_text(json.dumps(draft, indent=1), encoding="utf-8")
    model, body, thinking, tools = eval_coherence.load_agent_for_eval(COHERENCE_AGENT, coh_dir)
    report_path = coh_dir / "coherence_report.json"
    asyncio.run(eval_coherence.run_agent_to_file("coherence", report_path, model, body, thinking, tools))
    results = json.loads(report_path.read_text(encoding="utf-8")).get("results", [])
    flagged = [r for r in results if isinstance(r, dict) and r.get("pass") is False]
    return {
        "checked": len(results),
        "flags": len(flagged),
        "flagged_fields": sorted(f for r in flagged for f in (r.get("failed_fields") or ["?"])),
    }


def grade_rep(draft: dict) -> list[str]:
    graded = dict(draft)
    graded["preheader"] = graded.get("preheader") or "eval placeholder preheader"
    report = eval_graders.grade_selections(graded)
    return sorted(c.name for c in report.failures)


def summarise(label: str, values: list[float]) -> str:
    if not values:
        return f"{label}: no data"
    spread = f"min {min(values):g}  mean {stats.fmean(values):.2f}  max {max(values):g}"
    return f"{label}: {spread}  (n={len(values)})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=285)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--arms", default="TS", help="which arms to run, in order: T (tool loop), S (single turn)")
    args = ap.parse_args()
    arms = [a for a in args.arms.upper() if a in "TS"]
    if not arms:
        raise SystemExit("--arms must name T and/or S")

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    inputs = restore_inputs(conn, args.run)
    fan = write_fanout.build_branches(inputs)
    model, body, thinking, _tools = eval_coherence.load_agent(WRITE_AGENT)
    print(
        f"WRITE turns eval  run={args.run}  branches={len(fan.branches)} (dropped {len(fan.dropped)})  "
        f"model={model}  thinking={thinking['type']}  reps={args.reps}  arms={''.join(arms)}\n"
    )
    results: dict[str, list[dict]] = {a: [] for a in arms}
    for arm in arms:
        for rep in range(1, args.reps + 1):
            tag = f"{arm}{rep}"
            t0 = time.monotonic()
            draft, totals = asyncio.run(write_rep(arm, fan.branches, body, model, thinking))
            wrote = len(draft["must_know"]) + len(draft["should_know"])
            (WORK / str(args.run) / f"draft_{tag}.json").write_text(json.dumps(draft, indent=1), encoding="utf-8")
            secs = time.monotonic() - t0
            print(
                f"[{tag}] wrote {wrote}/{len(fan.branches)} in {secs:.0f}s  "
                f"tokens in={totals['input']} cw={totals['cache_write']} cr={totals['cache_read']} "
                f"out={totals['output']}  cost=${totals['cost']:.2f}"
                + (f"  FAILED {totals['failures']}" if totals["failures"] else "")
            )
            coherence = check_rep(inputs, tag, draft)
            l1 = grade_rep(draft)
            print(
                f"[{tag}] coherence flags {coherence['flags']}/{coherence['checked']} {coherence['flagged_fields']}  L1 failures {l1}"
            )
            results[arm].append(
                {"rep": rep, "wrote": wrote, "seconds": secs, **totals, "coherence": coherence, "l1_failures": l1}
            )
    (WORK / str(args.run) / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

    print("\n=== NOISE FLOOR (read this before the comparison) ===")
    for arm in arms:
        rows = results[arm]
        print(f"arm {arm}:")
        print("  " + summarise("coherence flags", [r["coherence"]["flags"] for r in rows]))
        print("  " + summarise("stories written", [r["wrote"] for r in rows]))
        print("  " + summarise("L1 failures", [len(r["l1_failures"]) for r in rows]))
        print("  " + summarise("cache_read tokens", [r["cache_read"] for r in rows]))
        print("  " + summarise("output tokens", [r["output"] for r in rows]))
        print("  " + summarise("cost (API-equiv $)", [round(r["cost"], 2) for r in rows]))
        print("  " + summarise("seconds", [round(r["seconds"]) for r in rows]))
    if len(arms) == 2:
        t_flags = [r["coherence"]["flags"] for r in results["T"]]
        s_flags = [r["coherence"]["flags"] for r in results["S"]]
        if t_flags and s_flags:
            verdict = (
                "within T's spread"
                if min(t_flags) <= stats.fmean(s_flags) <= max(t_flags)
                else ("BELOW T's minimum" if stats.fmean(s_flags) < min(t_flags) else "ABOVE T's maximum")
            )
            print(f"\nS mean flags {stats.fmean(s_flags):.2f} vs T range {min(t_flags)}-{max(t_flags)}: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
