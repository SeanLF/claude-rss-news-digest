"""RECAP Haiku-vs-Sonnet A/B harness: generate recaps across historical windows,
judge each for omission/fabrication, and L1-grade it -- producing the fixtures the
offline scoring + golden validation run against.

The pure helpers (prompt building, case assembly with injected judge/grade) are
unit-tested; ``generate_recap`` / ``run_ab`` make model calls and run in the
digest-newsroom container (DB at /app/data/digest.db, OAuth auth, no CLAUDECODE),
the same runtime production RECAP uses.

Faithful-replication note: production RECAP reads recent_rss_titles.csv via the
Read tool and writes recap.txt via Write. Here the identical task instructions are
issued as a system prompt with the titles inlined -- the summarisation task is the
same; only the file I/O is removed (it would only add a tool-use confound to a
model-capability A/B).

CLI:
    python -m eval_recap_ab --db /app/data/digest.db --n 12 --out /app/data/recap_ab.json
    python -m eval_recap_ab --smoke   # one window, both models -- path check
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

# RECAP task instructions, lifted from .claude/agents/recap.md minus the file I/O
# steps (titles are inlined instead of read from recent_rss_titles.csv).
RECAP_SYSTEM_PROMPT = """You are a recap summariser. Produce a 2-3 sentence thematic summary of recent news from the RSS titles provided.

Instructions:
1. Summarise the major themes in 2-3 sentences. Note any multi-day themes.
2. Do NOT reproduce specific headlines or titles. Use thematic language only.
3. Write paragraph format only -- no bullet points or lists.
4. Output is plain text, 2-3 sentences maximum.
5. If very few titles are provided, write a brief note that limited recent context is available.

Respond with ONLY the recap paragraph, no preamble."""

# Friendly name -> model id for the A/B arms.
MODELS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-4-6"}


def build_recap_user_prompt(titles: list[str]) -> str:
    listed = "\n".join(f"- {t}" for t in titles)
    return f"Recent RSS titles ({len(titles)}):\n{listed}"


def assemble_case(
    *,
    window_id: str,
    end_date: str,
    titles: list[str],
    recaps: dict[str, str],
    judge: Callable[[str, list[str]], tuple[list[str], list[str]]],
    grade: Callable[[str, list[str]], dict],
) -> dict:
    """Combine each model's recap with its judge verdict + L1 grade (pure)."""
    models: dict[str, dict] = {}
    for name, recap_text in recaps.items():
        missed, fabricated = judge(recap_text, titles)
        models[name] = {
            "recap": recap_text,
            "missed_themes": missed,
            "fabricated_themes": fabricated,
            "clean": not missed and not fabricated,
            "l1": grade(recap_text, titles),
        }
    return {"window_id": window_id, "end_date": end_date, "n_titles": len(titles), "models": models}


def summarize_ab(cases: list[dict], models: list[str]) -> dict:
    """Aggregate per-model defect totals and the paired per-window comparison.

    Binary "clean" is near-always-False (compressing a week into 3 sentences
    always drops something), so the discriminating signal is the paired
    missed-theme COUNT: in how many windows did each model omit strictly fewer
    themes than the other.
    """
    out: dict = {}
    for m in models:
        rows = [c["models"][m] for c in cases if m in c.get("models", {})]
        n = len(rows)
        defective = sum(1 for r in rows if not r["clean"])
        total_missed = sum(len(r["missed_themes"]) for r in rows)
        out[m] = {
            "n": n,
            "total_missed": total_missed,
            "total_fabricated": sum(len(r["fabricated_themes"]) for r in rows),
            "mean_missed": (total_missed / n) if n else 0.0,
            "defect_rate": (defective / n) if n else 0.0,
            "l1_pass_rate": (sum(1 for r in rows if r["l1"]["passed"]) / n) if n else 0.0,
        }

    if len(models) == 2:
        a, b = models
        a_fewer = b_fewer = ties = 0
        for c in cases:
            if a not in c["models"] or b not in c["models"]:
                continue
            ma = len(c["models"][a]["missed_themes"])
            mb = len(c["models"][b]["missed_themes"])
            if ma < mb:
                a_fewer += 1
            elif mb < ma:
                b_fewer += 1
            else:
                ties += 1
        out["_paired"] = {f"{a}_fewer_missed": a_fewer, f"{b}_fewer_missed": b_fewer, "ties": ties}
    return out


# --------------------------------------------------------------------------- #
# Model-call paths (integration; run in-container).
# --------------------------------------------------------------------------- #


def _with_retry(fn: Callable[[], object], *, attempts: int = 2, label: str = ""):
    """Call ``fn``; on TimeoutError/RuntimeError retry up to ``attempts`` total."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except (TimeoutError, RuntimeError) as e:  # transient SDK hang / non-zero exit
            last = e
            print(f"  retry {i + 1}/{attempts} after {type(e).__name__} on {label}", file=sys.stderr)
    raise last  # type: ignore[misc]


def generate_recap(titles: list[str], model: str, *, timeout: int = 240) -> str:
    """Generate one recap for ``titles`` with ``model`` via the Agent SDK wrapper."""
    import claude_cli

    text = claude_cli.run_sync(
        build_recap_user_prompt(titles),
        model=model,
        system_prompt=RECAP_SYSTEM_PROMPT,
        max_turns=1,
        timeout=timeout,
    ).strip()
    # A "successful" call can still return empty text (no-text turn, truncated
    # stream that closed clean). Scoring "" would have the judge report every
    # theme missed and silently bias the A/B -- fail hard so it retries/skips.
    if not text:
        raise RuntimeError(f"empty recap from {model} for {len(titles)} titles")
    return text


def select_pending_dates(dates: list, done_ids: set[str]) -> list:
    """The subset of ``dates`` whose ISO id is not already completed (resume support)."""
    return [d for d in dates if d.isoformat() not in done_ids]


def _grade_summary(recap_text: str, titles: list[str]) -> dict:
    from eval_stages import grade_recap

    report = grade_recap(recap_text, source_titles=titles)
    return {"passed": report.passed, "failed": _names_failed(report)}


def _names_failed(report) -> list[str]:
    return [c.name for c in report.checks if not c.passed]


def _load_existing(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text(encoding="utf-8")).get("cases", [])
    except (OSError, json.JSONDecodeError) as e:
        # Distinguish "no file yet" (start fresh) from "file exists but is
        # corrupt". The latter usually means a crash mid-write; treating it as
        # empty would silently discard prior windows and overwrite them. Fail
        # loudly so the operator moves it aside deliberately.
        raise RuntimeError(
            f"resume file {out_path} exists but is unreadable "
            f"({type(e).__name__}: {e}); move it aside before re-running"
        ) from e


def _save_cases(out_path: Path, cases: list[dict]) -> None:
    """Atomically persist ``cases`` so a crash mid-write can't corrupt the resume
    file: write a sibling tmp then ``replace`` (atomic on the same filesystem)."""
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)


def run_ab(
    db_path: str | Path,
    *,
    n_windows: int,
    out_path: str | Path,
    models: dict[str, str] | None = None,
    title_cap: int = 300,
    min_titles: int = 80,
    spacing_days: int = 9,
    judge_timeout: int = 240,
) -> list[dict]:
    """Generate + judge + grade recaps across ``n_windows`` windows.

    Resilient: saves after every window (so a crash keeps prior work), retries a
    transient call once, and SKIPS a window that still fails rather than aborting
    the whole run. Re-running resumes -- completed windows in ``out_path`` are
    skipped.
    """
    from eval_recap_judge import JUDGE_MODEL, judge_recap, prepare_titles
    from eval_recap_windows import build_window, load_shown_rows, select_window_dates

    models = models or MODELS
    out_path = Path(out_path)
    rows = load_shown_rows(db_path)
    dates = select_window_dates(rows, n=n_windows, min_titles=min_titles, spacing_days=spacing_days)

    cases = _load_existing(out_path)
    pending = select_pending_dates(dates, {c["window_id"] for c in cases})
    print(f"{len(dates)} windows, {len(cases)} already done, {len(pending)} pending", file=sys.stderr)

    def judge(recap_text: str, titles: list[str]) -> tuple[list[str], list[str]]:
        return _with_retry(
            lambda: judge_recap(recap_text, titles, model=JUDGE_MODEL, title_cap=title_cap, timeout=judge_timeout),
            label="judge",
        )

    for d in pending:
        window = build_window(rows, end_date=d, days=7)
        titles = prepare_titles([w["headline"] for w in window], cap=title_cap)
        try:
            recaps = {
                name: _with_retry(lambda mid=mid, t=titles: generate_recap(t, mid), label=f"recap:{name}")
                for name, mid in models.items()
            }
            case = assemble_case(
                window_id=d.isoformat(),
                end_date=d.isoformat(),
                titles=titles,
                recaps=recaps,
                judge=judge,
                grade=_grade_summary,
            )
        except Exception as e:
            print(f"[{d.isoformat()}] SKIPPED after error: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        cases.append(case)
        _save_cases(out_path, cases)
        print(
            f"[{d.isoformat()}] {len(titles)} titles  "
            + "  ".join(f"{name}:miss{len(case['models'][name]['missed_themes'])}" for name in models),
            file=sys.stderr,
        )

    # Reconcile attempted vs scored so silent window attrition (a model/judge
    # that fails on the hard windows) can't masquerade as a clean result.
    scored_ids = {c["window_id"] for c in cases}
    missing = [d.isoformat() for d in dates if d.isoformat() not in scored_ids]
    if missing:
        print(
            f"WARNING: {len(dates)} windows attempted, {len(dates) - len(missing)} scored, "
            f"{len(missing)} SKIPPED ({', '.join(missing)}) -- aggregate stats are over a "
            f"partial, possibly biased sample. Investigate before trusting the A/B.",
            file=sys.stderr,
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="RECAP Haiku-vs-Sonnet A/B")
    parser.add_argument("--db", default="/app/data/digest.db")
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--out", default="/app/data/recap_ab.json")
    parser.add_argument("--smoke", action="store_true", help="one window only (path check)")
    args = parser.parse_args()

    cases = run_ab(args.db, n_windows=1 if args.smoke else args.n, out_path=args.out)
    print(f"wrote {len(cases)} cases -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
