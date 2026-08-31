"""CLUSTER extraction-call sweep: model x thinking, over REAL 40-article batches.

The global `thinking: disabled` was originally written FOR this stage: extended
thinking over ~460 articles in ONE call once tripped the 32k output-token ceiling
and aborted a run. It is now a batched extract (`_EXTRACT_BATCH = 40`, bounded
concurrency), so the per-call output is far smaller and that failure may no longer
be reachable. This measures whether it is.

The LOAD-BEARING question is the ceiling, not quality: if adaptive thinking pushes
any batch near the output cap, the recommendation is "do not", whatever quality
says. A truncated response does not raise -- it parses to zero items and the batch
title-only-falls-back -- so the ceiling shows up here as `yield == 0`, and that is
tracked per batch alongside the raw token counts.

Reuses the PRODUCTION prompt builder and parsers verbatim (`build_extract_prompt`,
`parse_extract_items`, `items_for_batch`, `_duplicate_count`) so a prompt or parser
change is reflected without editing this file.

Usage:  run.sh sweep_extract.py --runs 279 280 --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/sweep")
from sweep_common import OUT, article_csv_names, build_replay, record, run_arm, usage_row  # noqa: E402

sys.path.insert(0, "/app/src")
from cluster_extractjoin import (  # noqa: E402
    _EXTRACT_BATCH,
    _EXTRACT_CONCURRENCY,
    EXTRACT_SYSTEM,
    _duplicate_count,
    _thinking_for,
    build_extract_prompt,
    items_for_batch,
    load_articles,
    parse_extract_items,
)

JSONL = OUT / "extract.jsonl"

# Output-token headroom references. 32k is the ceiling the original incident hit;
# 64k is the current per-response cap for the Sonnet family. Reported as a ratio so
# "did any batch get close" is answerable without re-deriving it.
CEILING_INCIDENT = 32000
CEILING_CURRENT = 64000

ARMS = [
    ("s46/disabled", "claude-sonnet-4-6", {"type": "disabled"}),  # control = production today
    ("s46/adaptive", "claude-sonnet-4-6", {"type": "adaptive"}),
    ("s5/disabled", "claude-sonnet-5", {"type": "disabled"}),
    ("s5/adaptive", "claude-sonnet-5", {"type": "adaptive"}),  # == _thinking_for(next-gen) today
]


async def one_batch(*, model, thinking, prompt, batch, arts) -> dict:
    """Run one extraction batch and score it with the production parsers."""
    res, wall = await run_arm(
        model=model, body=EXTRACT_SYSTEM, thinking=thinking, tools=[], out_path=None,
        prompt=prompt, max_turns=1, idle_timeout=300.0,
    )
    parsed = parse_extract_items(res.text)
    scoped = items_for_batch(parsed, batch)
    u = usage_row(res, wall)
    # Items the model returned that name an id this batch never asked about: the
    # renumbering fault items_for_batch exists to contain. Counted, not tolerated.
    wanted = set(batch)
    miskeyed = sum(1 for it in parsed if it.get("article_id") not in wanted)
    empty_event = sum(1 for it in scoped if not str(it.get("primary_event") or "").strip())
    return {
        "n_batch": len(batch),
        "n_parsed": len(parsed),
        "n_scoped": len(scoped),
        "yield": round(len(scoped) / len(batch), 4) if batch else 0.0,
        "zero_yield": len(scoped) == 0,
        "duplicates": _duplicate_count(parsed, batch),
        "miskeyed": miskeyed,
        "empty_primary_event": empty_event,
        "text_chars": len(res.text),
        "pct_of_32k": round(u["output_tokens"] / CEILING_INCIDENT, 4),
        "pct_of_64k": round(u["output_tokens"] / CEILING_CURRENT, 4),
        **u,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-batches", type=int, default=0, help="0 = every batch in the run")
    # Prod runs _EXTRACT_CONCURRENCY (4). The sweep may go wider: it is measuring
    # per-call token/quality behaviour, and concurrency does not change either.
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args()

    print(f"EXTRACT sweep: batch={_EXTRACT_BATCH} concurrency={_EXTRACT_CONCURRENCY} "
          f"prod thinking for sonnet-4-6 = {_thinking_for('claude-sonnet-4-6')}, "
          f"for sonnet-5 = {_thinking_for('claude-sonnet-5')}", flush=True)

    for run_id in args.runs:
        replay = Path(f"/app/sweep/replay/extract_{run_id}")
        build_replay(run_id, replay, article_csv_names(run_id), sources_csv=False)
        arts = load_articles(replay)
        ids = list(arts.keys())
        batches = [ids[i : i + _EXTRACT_BATCH] for i in range(0, len(ids), _EXTRACT_BATCH)]
        if args.max_batches:
            batches = batches[: args.max_batches]
        prompts = [build_extract_prompt(b, arts) for b in batches]
        print(f"\n=== run {run_id}: {len(ids)} articles -> {len(batches)} batches "
              f"(prompt chars: min={min(map(len, prompts))} max={max(map(len, prompts))})", flush=True)

        for label, model, thinking in ARMS:
            for rep in range(args.reps):
                sem = asyncio.Semaphore(args.concurrency or _EXTRACT_CONCURRENCY)

                async def guarded(n: int, b: list[str], p: str, _m=model, _t=thinking) -> dict:
                    async with sem:
                        try:
                            return {"batch": n, "ok": True, **await one_batch(
                                model=_m, thinking=_t, prompt=p, batch=b, arts=arts)}
                        except Exception as e:  # noqa: BLE001
                            return {"batch": n, "ok": False, "error": repr(e)[:300], "n_batch": len(b)}

                results = await asyncio.gather(
                    *(guarded(n, b, p) for n, (b, p) in enumerate(zip(batches, prompts, strict=True)))
                )
                for r in results:
                    record(JSONL, {"stage": "extract", "run_id": run_id, "arm": label, "model": model,
                                   "thinking": thinking["type"], "rep": rep, **r})
                ok = [r for r in results if r["ok"]]
                if not ok:
                    print(f"  {label} r{rep}: ALL {len(results)} BATCHES FAILED", flush=True)
                    continue
                print(
                    f"  {label} r{rep}: batches={len(ok)}/{len(results)} "
                    f"yield={sum(r['n_scoped'] for r in ok) / sum(r['n_batch'] for r in ok):.3f} "
                    f"zero_yield={sum(r['zero_yield'] for r in ok)} "
                    f"dupes={sum(r['duplicates'] for r in ok)} miskeyed={sum(r['miskeyed'] for r in ok)} "
                    f"out_max={max(r['output_tokens'] for r in ok)} "
                    f"({max(r['pct_of_32k'] for r in ok) * 100:.1f}% of 32k) "
                    f"think_max={max(r['thinking_tokens'] for r in ok)} "
                    f"${sum(r['cost_usd'] for r in ok):.3f} "
                    f"wall_max={max(r['wall_s'] for r in ok):.0f}s", flush=True)
    print(f"\nwrote {JSONL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
