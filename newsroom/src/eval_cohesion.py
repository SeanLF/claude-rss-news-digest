"""Replay the cohesion gate over archived runs and score it against blind labels.

Task 4 of docs/2026-09-03-cohesion-gate-plan.md. For each run, the gate runs REPS times over
the run's selected clusters (the real cohesion.run_cohesion_stage, the real prompt), and every
verdict is scored against docs/2026-09-03-cohesion-gate-labels.json: labels written from
titles before any judge ran, by one reader, not ground truth.

Per cluster: event-count agreement, stray-set Jaccard, over-splits (labelled same-event ids
the judge put outside the dominant group), and whether each known must-separate id was
separated. The gate the plan states is read on the MODAL verdict across reps: count
agreement >= 80% of clusters, zero over-splits, every known case separated. The metric is
agreement, never "fewer multi-event clusters" (the 2026-09-01 embedding gate's metric was
maximised by fragmentation).

Makes REAL model calls on the subscription -- opt-in, never in CI.
Usage: bin/eval-cohesion [--runs 284,285] [--reps 3] [--model claude-sonnet-4-6]
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
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/app/src")

import cohesion

DB = Path("/app/data/digest.db")
WORK = Path("/app/data/eval-cohesion")
LABELS = Path("/app/eval-labels.json")


def restore_inputs(conn: sqlite3.Connection, run: int) -> Path:
    rows = conn.execute("SELECT artifact_name, content FROM run_artifacts WHERE run_id=?", (run,)).fetchall()
    keep = {
        n: c
        for n, c in rows
        if n in ("selected.json", "clusters.json") or (n.startswith("articles_") and n.endswith(".csv"))
    }
    for required in ("selected.json", "clusters.json"):
        if required not in keep:
            raise SystemExit(f"run {run} has no archived {required} -- nothing to replay")
    inputs = WORK / str(run)
    if inputs.exists():
        shutil.rmtree(inputs)
    inputs.mkdir(parents=True)
    for name, content in keep.items():
        (inputs / name).write_text(content, encoding="utf-8")
    return inputs


def score_verdict(cluster_ids: list[str], verdict: dict, label: dict, *, must_separate: list[str]) -> dict:
    """One cluster, one verdict, one label."""
    events = verdict.get("events") if verdict.get("applied") else None
    n_events = len(events) if events else 1
    judge_strays = set(verdict.get("strays") or []) if verdict.get("applied") else set()
    label_strays = set(label.get("strays") or [])
    label_dominant = set(cluster_ids) - label_strays
    union = judge_strays | label_strays
    jaccard = (len(judge_strays & label_strays) / len(union)) if union else 1.0
    return {
        "n_events": n_events,
        "count_agrees": n_events == label.get("n_events"),
        "jaccard": round(jaccard, 3),
        "over_splits": sorted(judge_strays & label_dominant),
        "missed_strays": sorted(label_strays - judge_strays),
        "separated": {i: i in judge_strays for i in must_separate},
    }


def modal_verdict(verdicts: list[dict]) -> dict:
    """The most common (applied, strays) across reps; ties go to the first seen."""
    keyed = Counter((v.get("applied") is True, tuple(sorted(v.get("strays") or []))) for v in verdicts)
    (applied, strays), _ = keyed.most_common(1)[0]
    for v in verdicts:
        if (v.get("applied") is True, tuple(sorted(v.get("strays") or []))) == (applied, strays):
            return v
    return verdicts[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="284,285")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--labels", default=str(LABELS))
    args = ap.parse_args()
    runs = [int(r) for r in args.runs.split(",") if r.strip()]
    labels_doc = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    labels = {(lb["run"], lb["cluster_index"]): lb for lb in labels_doc["labels"]}
    known = {(285, k["cluster_index"]): k["must_separate"] for k in labels_doc.get("known_cases_285", {}).values()}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print(f"COHESION gate replay  runs={runs}  reps={args.reps}  model={args.model}  labels={len(labels)}\n")

    per_cluster: dict[tuple[int, int], list[dict]] = {}
    cluster_ids: dict[tuple[int, int], list[str]] = {}
    totals = {"cost": 0.0, "seconds": 0.0, "calls": 0}
    for run in runs:
        inputs = restore_inputs(conn, run)
        for rep in range(1, args.reps + 1):
            t0 = time.monotonic()
            row = asyncio.run(cohesion.run_cohesion_stage(inputs, model=args.model, cwd="/app"))
            secs = time.monotonic() - t0
            doc = json.loads((inputs / cohesion.COHESION_ARTIFACT).read_text(encoding="utf-8"))
            shutil.copyfile(inputs / cohesion.COHESION_ARTIFACT, inputs / f"cluster_cohesion.{rep}.json")
            totals["cost"] += row.get("api_cost_usd", 0.0)
            totals["seconds"] += secs
            totals["calls"] += 1
            print(
                f"[run {run} rep {rep}] outcome={doc['outcome']} judged={doc['judged']} split={doc['split']} "
                f"strays_removed={doc['strays_removed']} cost=${row.get('api_cost_usd', 0.0):.3f} {secs:.0f}s"
            )
            for v in doc["verdicts"]:
                key = (run, v["cluster_index"])
                cluster_ids[key] = v["article_ids"]
                per_cluster.setdefault(key, []).append(v)
                if key in labels:
                    s = score_verdict(v["article_ids"], v, labels[key], must_separate=known.get(key, []))
                    flag = "" if s["count_agrees"] else "  <-- count differs"
                    over = f"  OVER-SPLIT {s['over_splits']}" if s["over_splits"] else ""
                    print(
                        f"    cluster {v['cluster_index']:>3} n={len(v['article_ids']):>2}: judge {s['n_events']} events, "
                        f"label {labels[key]['n_events']}; jaccard {s['jaccard']}{flag}{over}"
                    )

    print("\n=== MODAL VERDICT vs LABELS ===")
    agree = total = over_total = 0
    jaccards: list[float] = []
    separated_all: dict[str, bool] = {}
    for key, verdicts in per_cluster.items():
        if key not in labels:
            continue
        m = modal_verdict(verdicts)
        s = score_verdict(cluster_ids[key], m, labels[key], must_separate=known.get(key, []))
        total += 1
        agree += int(s["count_agrees"])
        over_total += len(s["over_splits"])
        jaccards.append(s["jaccard"])
        for i, ok in s["separated"].items():
            separated_all[f"{key[0]}/{key[1]}/{i}"] = ok
        mark = "ok " if s["count_agrees"] else "diff"
        extra = f"  over-split {s['over_splits']}" if s["over_splits"] else ""
        missed = f"  missed {s['missed_strays']}" if s["missed_strays"] else ""
        print(
            f"  [{mark}] run {key[0]} cluster {key[1]:>3}: judge {s['n_events']} vs label {labels[key]['n_events']}, jaccard {s['jaccard']}{extra}{missed}"
        )
    print(f"\nevent-count agreement: {agree}/{total} ({100.0 * agree / max(total, 1):.0f}%)")
    print(
        f"stray-set jaccard: mean {stats.fmean(jaccards):.2f}  median {stats.median(jaccards):.2f}"
        if jaccards
        else "no scored clusters"
    )
    print(f"over-splits on the modal verdict: {over_total}")
    print("known cases separated: " + ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in separated_all.items()))
    print(
        f"cost: ${totals['cost']:.3f} over {totals['calls']} gate runs (${totals['cost'] / max(totals['calls'], 1):.3f}/run), "
        f"{totals['seconds'] / max(totals['calls'], 1):.0f}s/run"
    )
    gate = agree / max(total, 1) >= 0.8 and over_total == 0 and all(separated_all.values())
    print(f"\nGATE ({'>=80% count agreement, 0 over-splits, all known cases separated'}): {'PASS' if gate else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
