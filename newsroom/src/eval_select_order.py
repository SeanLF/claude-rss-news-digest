"""Does SELECT's pick depend on the ORDER clusters are listed in, or only on sampling noise?

arXiv 2608.26762 (2026-08-27) measured that an LLM scorer's retained set moves by 16-34% under
a permutation of the candidates alone, and that no prompt-time change removed it. Our own
number for SELECT (rep-to-rep Jaccard 0.24-0.34, docs/2026-09-03-clustering-pocs.md) was taken
with the input in a fixed order, so it conflates the two. This harness separates them:

  arm ``fixed``     N reps of the real select.md on the archived clusters.json as shipped
  arm ``shuffled``  N reps, each on a fresh uniform permutation of the ``clusters`` array
  arm ``sorted``    N reps, clusters in size-descending order (stable), the explicit version of
                    the order the join happens to emit (by first article id, which front-loads
                    big clusters: run 298's size>=10 clusters all sit in the first 105 of 289)

Everything else (articles_*.csv, recap.txt, sources.csv, yesterday_headlines.txt) is identical
across reps. Each rep's answer is mapped back to ORIGINAL cluster indices -- through the
permutation, and by the story's citations rather than the positional ``cluster_index`` it
wrote, because that index drifts on ~14% of stories (write_fanout.resolve_cluster_index) --
and the within-arm pairwise Jaccard is the measurement. The fixed arm is the reference's
self-agreement band; if the shuffled arm is not materially below it, order is not a component
and order-averaging (k permutations, vote) has nothing to buy.

Makes REAL model calls on the subscription (~$0.4 a rep): opt-in, never in CI.
``bin/eval-select-order`` runs it through the production agent-sdk path in Docker.

Negative control (no model call): ``--check`` pushes the ARCHIVED selected.json through a
permutation as if a model had answered in the permuted space and requires it to canonicalise
to the same clusters. A mapping bug fails there before a cent is spent.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import shutil
import statistics
from pathlib import Path

from write_fanout import resolve_cluster_index

AGENT = Path("/app/.claude/agents/select.md")
FIXTURES = Path("/app/eval-fixtures")
WORK = Path("/app/eval-work")
ARMS = ("fixed", "shuffled", "sorted")
# What SELECT reads. The archived selected.json is deliberately NOT copied into a rep.
_INPUT_GLOBS = (
    "clusters.json",
    "recap.txt",
    "articles_*.csv",
    "sources.csv",
    "weekly_recap.txt",
    "yesterday_headlines.txt",
)


# --------------------------------------------------------------------------- #
# Pure parts (tested without model calls).
# --------------------------------------------------------------------------- #


def permute_clusters(clusters: list, rng: random.Random | None) -> tuple[list, list[int]]:
    """Return (permuted clusters, perm) where ``perm[new_position] == original_index``.
    ``rng=None`` is the identity, i.e. the fixed arm."""
    perm = list(range(len(clusters)))
    if rng is not None:
        rng.shuffle(perm)
    return [clusters[i] for i in perm], perm


def order_for(arm: str, clusters: list, rng: random.Random) -> list[int]:
    """The permutation an arm reads: identity, uniform random, or size-descending (stable, so
    equal sizes keep the archived relative order and the arm is deterministic)."""
    if arm == "fixed":
        return list(range(len(clusters)))
    if arm == "shuffled":
        return permute_clusters(clusters, rng)[1]
    if arm == "sorted":
        return sorted(range(len(clusters)), key=lambda i: -len(clusters[i].get("article_ids") or []))
    raise ValueError(f"unknown arm {arm!r}; choose from {ARMS}")


def to_original(perm: list[int], positions: list[int]) -> list[int]:
    """Map positions in the permuted list back to original cluster indices."""
    return [perm[p] for p in positions]


def _resolve_picks(selected: dict, clusters: list) -> dict:
    """Each tier's picks as POSITIONS in ``clusters``, resolved by citations (the production
    rule) rather than the written cluster_index. ``drifted`` counts stories whose written index
    disagreed with their citations; ``unresolved`` counts stories citing nothing any cluster
    holds (dropped)."""
    out: dict = {"drifted": 0, "unresolved": 0}
    for tier in ("must_know", "should_know"):
        picks: set[int] = set()
        for item in selected.get(tier) or []:
            if not isinstance(item, dict):
                continue
            ids = [a for a in (item.get("article_ids") or []) if isinstance(a, str)]
            pos = resolve_cluster_index(clusters, item.get("cluster_index"), ids)
            if pos is None:
                out["unresolved"] += 1
                continue
            if item.get("cluster_index") != pos:
                out["drifted"] += 1
            picks.add(pos)
        out[tier] = picks
    return out


def canonical_selection(selected: dict, work_clusters: list, perm: list[int]) -> dict:
    """One rep's answer as sets of ORIGINAL cluster indices, per tier and overall: positions in
    the permuted list the model actually read, mapped back through ``perm``."""
    picks = _resolve_picks(selected, work_clusters)
    out: dict = {"drifted": picks["drifted"], "unresolved": picks["unresolved"]}
    for tier in ("must_know", "should_know"):
        out[tier] = frozenset(to_original(perm, sorted(picks[tier])))
    out["all"] = out["must_know"] | out["should_know"]
    return out


def require_picks(canon: dict, label: str) -> None:
    """Two empty picks score Jaccard 1.0, so a degenerate rep must raise rather than read as
    perfect stability; the must_know tier is scored on its own and needs its own check."""
    if not canon["all"] or not canon["must_know"]:
        raise RuntimeError(
            f"{label}: SELECT picked nothing resolvable ({len(canon['all'])} all, {len(canon['must_know'])} must_know)"
        )


def pairwise_jaccard(sets: list[frozenset]) -> list[float]:
    """Jaccard over every unordered pair, in itertools.combinations order. Two empty sets agree."""
    vals = []
    for a, b in itertools.combinations(sets, 2):
        union = a | b
        vals.append(1.0 if not union else len(a & b) / len(union))
    return vals


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def summarise(reps: dict[str, list[dict]], n_clusters: int) -> dict:
    """Per-arm within-arm agreement, the cross-arm agreement, and which clusters every rep of an
    arm picked (the stable core the 2026-09-03 side finding described)."""
    out: dict = {}
    for arm, rows in reps.items():
        alls = [r["all"] for r in rows]
        mks = [r["must_know"] for r in rows]
        freq = {i: sum(1 for s in alls if i in s) for i in range(n_clusters)}
        aj = pairwise_jaccard(alls)
        mj = pairwise_jaccard(mks)
        out[arm] = {
            "reps": len(rows),
            "all_jaccard_mean": _mean(aj),
            "all_jaccard_min": min(aj) if aj else None,
            "all_jaccard_max": max(aj) if aj else None,
            "must_know_jaccard_mean": _mean(mj),
            "picks_per_rep": [len(s) for s in alls],
            "always_selected": sorted(i for i, n in freq.items() if rows and n == len(rows)),
            "ever_selected": sum(1 for n in freq.values() if n),
            "drifted": sum(r["drifted"] for r in rows),
            "unresolved": sum(r["unresolved"] for r in rows),
            "stories": sum(r.get("stories", 0) for r in rows),
            "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
        }
    arms = [a for a in ARMS if a in reps]
    out["tests"] = {}
    for a, b in itertools.combinations(arms, 2):
        out[f"cross_{a}_{b}_all_jaccard_mean"] = _cross([r["all"] for r in reps[a]], [r["all"] for r in reps[b]])
        if len(reps[a]) >= 2 and len(reps[b]) >= 2:
            out["tests"][f"{a}_vs_{b}"] = {
                metric: permutation_tests([r[metric] for r in reps[a]], [r[metric] for r in reps[b]])
                for metric in ("all", "must_know")
            }
    return out


def _within(sets: list[frozenset]) -> float:
    return statistics.fmean(pairwise_jaccard(sets))


def _cross(a: list[frozenset], b: list[frozenset]) -> float:
    return statistics.fmean(pairwise_jaccard([x, y])[0] for x in a for y in b)


def permutation_tests(a: list[frozenset], b: list[frozenset]) -> dict:
    """Two exact tests over every equal split of the pooled reps (exchangeable under the null
    that the arm does not matter).

    ``gap``: |within(a) - within(b)|, two-sided. Sees an arm that adds VARIANCE to the pick.
    ``shift``: mean within minus cross, one-sided. Sees an arm that MOVES the pick to different
    clusters even when each arm is as self-consistent as the other; the gap test is blind to
    that, which is how the second write-up of run 298 concluded "no order effect" from a data
    set in which order moved about one of 16 picks (review of 4e73378).
    Also reports the smallest |gap| that reaches p <= 0.05, so a null result comes with what it
    could have seen."""
    pool = a + b
    n = len(a)
    obs_gap = abs(_within(a) - _within(b))
    obs_shift = (_within(a) + _within(b)) / 2 - _cross(a, b)
    gaps, ge_gap, ge_shift, total = [], 0, 0, 0
    for comb in itertools.combinations(range(len(pool)), n):
        x = [pool[i] for i in comb]
        y = [pool[i] for i in range(len(pool)) if i not in comb]
        g = abs(_within(x) - _within(y))
        gaps.append(g)
        total += 1
        ge_gap += g >= obs_gap - 1e-12
        ge_shift += ((_within(x) + _within(y)) / 2 - _cross(x, y)) >= obs_shift - 1e-12
    gaps.sort()
    # Smallest observed gap whose two-sided p would be <= 0.05.
    threshold = next((g for g in gaps if sum(1 for h in gaps if h >= g - 1e-12) / total <= 0.05), None)
    return {
        "gap": round(_within(a) - _within(b), 4),
        "gap_p_two_sided": round(ge_gap / total, 4),
        "gap_threshold_p05": round(threshold, 4) if threshold is not None else None,
        "shift": round(obs_shift, 4),
        "shift_p_one_sided": round(ge_shift / total, 4),
        "splits": total,
    }


def prepare_workdir(fixtures: Path, work: Path, perm: list[int] | None) -> list[int]:
    """A rep's private input dir: SELECT's inputs copied, clusters reordered by ``perm``
    (identity when None), the permutation recorded beside the dir, the archived answer left
    behind. Every file INSIDE the dir is then identical across reps except clusters.json. The file
    is serialised exactly as ``cluster_extractjoin`` serialises it."""
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    article_rows = 0
    for pattern in _INPUT_GLOBS:
        for src in sorted(fixtures.glob(pattern)):
            if src.name != "clusters.json":
                shutil.copy2(src, work / src.name)
                if pattern == "articles_*.csv":
                    # Data rows, not files: a header-only CSV is the same broken input as none.
                    article_rows += max(0, len(src.read_text(encoding="utf-8").splitlines()) - 1)
    if not article_rows:
        raise RuntimeError(
            f"{fixtures}: no article rows in articles_*.csv -- SELECT would answer from clusters.json alone"
        )
    data = json.loads((fixtures / "clusters.json").read_text(encoding="utf-8"))
    clusters = data["clusters"]
    if perm is None:
        perm = list(range(len(clusters)))
    if sorted(perm) != list(range(len(clusters))):
        raise ValueError("perm is not a permutation of the cluster positions")
    # Byte-for-byte the production writer (cluster_extractjoin: ``json.dumps(out, indent=2)``,
    # ASCII escapes and all), so the fixed arm reads exactly what SELECT reads in prod.
    (work / "clusters.json").write_text(
        json.dumps({**data, "clusters": [clusters[i] for i in perm]}, indent=2), encoding="utf-8"
    )
    # Beside the input dir, not inside it, so a listing of what the model reads does not name the arm.
    work.with_name(work.name + ".permutation.json").write_text(json.dumps(perm), encoding="utf-8")
    return perm


def negative_control(fixtures: Path, seed: int) -> dict:
    """Instrument check without a model: rewrite the archived answer into a permuted space
    (positions AND citations untouched -- citations are order-free) and require the canonical
    form to equal the unpermuted one."""
    clusters = json.loads((fixtures / "clusters.json").read_text(encoding="utf-8"))["clusters"]
    archived = json.loads((fixtures / "selected.json").read_text(encoding="utf-8"))
    # The unpermuted answer is read WITHOUT the inverse mapping, so a broken to_original cannot
    # cancel out of both sides of the comparison.
    direct = _resolve_picks(archived, clusters)
    original = {"all": direct["must_know"] | direct["should_know"], "must_know": frozenset(direct["must_know"])}
    permuted, perm = permute_clusters(clusters, random.Random(seed))
    inverse = {orig: pos for pos, orig in enumerate(perm)}
    as_if = {
        tier: [
            {**item, "cluster_index": inverse.get(ci, ci) if isinstance(ci := item.get("cluster_index"), int) else ci}
            for item in (archived.get(tier) or [])
            if isinstance(item, dict)
        ]
        for tier in ("must_know", "should_know")
    }
    round_trip = canonical_selection(as_if, permuted, perm)
    o, r = sorted(original["all"]), sorted(round_trip["all"])
    # An archived answer with nothing resolvable would pass vacuously; that is a broken fixture.
    ok = bool(o) and o == r and original["must_know"] == round_trip["must_know"]
    return {"ok": ok, "original": o, "round_trip": r}


# --------------------------------------------------------------------------- #
# Model-calling parts (Docker, production SDK path).
# --------------------------------------------------------------------------- #


async def _run_rep(
    arm: str, i: int, fixtures: Path, work_root: Path, agent: Path, seed: int, model: str | None, sem
) -> dict:
    import eval_coherence  # /app/src; the shared load-and-redirect + run helpers
    import orchestrate

    work = work_root / f"{arm}_{i}"
    base = json.loads((fixtures / "clusters.json").read_text(encoding="utf-8"))["clusters"]
    perm = prepare_workdir(fixtures, work, order_for(arm, base, random.Random(seed * 1000 + i)))
    model_name, body, thinking, tools = eval_coherence.load_agent_for_eval(agent, work, model)
    async with sem:
        res = await eval_coherence.run_agent_to_file(
            f"select/{arm}/{i}", work / "selected.json", model_name, body, thinking, tools
        )
    orchestrate.validate_selected(work)
    selected = json.loads((work / "selected.json").read_text(encoding="utf-8"))
    work_clusters = json.loads((work / "clusters.json").read_text(encoding="utf-8"))["clusters"]
    canon = canonical_selection(selected, work_clusters, perm)
    require_picks(canon, f"{arm} rep {i}")
    canon["stories"] = sum(len(selected.get(t) or []) for t in ("must_know", "should_know"))
    canon["cost_usd"] = float(res.total_cost_usd)  # StageResult field; an SDK rename must fail loudly
    print(
        f"  {arm} rep {i}: {len(canon['all'])} picks ({len(canon['must_know'])} must_know), "
        f"drifted {canon['drifted']}, unresolved {canon['unresolved']}, ${canon['cost_usd']:.3f}",
        flush=True,
    )
    return canon


async def _run_all(args) -> dict[str, list[dict]]:
    sem = asyncio.Semaphore(max(1, args.concurrency))
    order = rep_order(parse_arms(args.arms), args.reps)
    arms = list(dict.fromkeys(arm for arm, _ in order))
    results = await asyncio.gather(
        *(
            _run_rep(arm, i, Path(args.fixtures), Path(args.work), Path(args.agent), args.seed, args.model, sem)
            for arm, i in order
        )
    )
    out: dict[str, list[dict]] = {arm: [] for arm in arms}
    for (arm, _), canon in zip(order, results, strict=True):
        out[arm].append(canon)
    return out


def parse_arms(spec: str) -> list[str]:
    arms = [a.strip() for a in spec.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown or not arms:
        raise SystemExit(f"--arms: unknown {unknown or 'empty'}; choose from {','.join(ARMS)}")
    if len(set(arms)) != len(arms):
        # Two reps of one arm would share a work dir and score Jaccard 1.0 against themselves.
        raise SystemExit(f"--arms: duplicate arm in {spec!r}")
    return arms


def rep_order(arms: list[str], reps: int) -> list[tuple[str, int]]:
    """Round-robin across arms: rep i of every arm before rep i+1 of any, so a backend drift
    over the run's wall clock lands on all arms alike instead of on whichever ran last. Tasks
    are created in this order; asyncio starts them in creation order and the semaphore is FIFO,
    which is what the first-pass mtimes (fixed all first) and the second-pass mtimes
    (interleaved) both confirmed."""
    return [(arm, i) for i in range(reps) for arm in arms]


def _jsonable(summary: dict) -> dict:
    return json.loads(json.dumps(summary, default=lambda o: sorted(o) if isinstance(o, frozenset) else str(o)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixtures", default=str(FIXTURES), help="dir holding one archived run's SELECT inputs")
    ap.add_argument("--work", default=str(WORK), help="where each rep's input dir and answer are kept")
    ap.add_argument("--agent", default=str(AGENT))
    ap.add_argument("--reps", type=int, default=4, help="reps PER ARM")
    ap.add_argument("--arms", default="fixed,shuffled", help=f"comma list from {','.join(ARMS)}")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--concurrency", type=int, default=2, help="the OAuth token rate-limits under burst")
    ap.add_argument("--model", default=None, help="override select.md's frontmatter model")
    ap.add_argument("--check", action="store_true", help="negative control only: no model calls")
    ap.add_argument("--rescore", default=None, help="recompute the summary from a stored summary.json; no model calls")
    ap.add_argument(
        "--in-place", action="store_true", help="with --rescore: overwrite the stored file (default: print only)"
    )
    args = ap.parse_args()

    if args.rescore:
        # Default is read-only: the stored file is the evidence a doc cites, and re-checking
        # evidence must not rewrite it. A changed summarise() shows up as a printed difference.
        stored = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        reps = {
            arm: [{**r, "all": frozenset(r["all"]), "must_know": frozenset(r["must_know"])} for r in rows]
            for arm, rows in stored["reps"].items()
        }
        n_clusters = max((i for rows in reps.values() for r in rows for i in r["all"]), default=0) + 1
        summary = _jsonable(summarise(reps, n_clusters))
        _print_summary(summary)
        same = summary == stored.get("summary")
        print(f"\n  stored summary block {'matches' if same else 'DIFFERS FROM'} the recomputed one")
        if args.in_place and not same:
            stored["summary"] = summary
            Path(args.rescore).write_text(json.dumps(stored, indent=2), encoding="utf-8")
            print(f"  rewrote {args.rescore}")
        return 0 if same or args.in_place else 1

    fixtures = Path(args.fixtures)
    nc = negative_control(fixtures, args.seed)
    print(f"negative control: {'ok' if nc['ok'] else 'FAILED'}  archived picks {nc['original']}")
    if not nc["ok"]:
        print(f"  round trip gave {nc['round_trip']}")
        return 2
    if args.check:
        return 0

    n_clusters = len(json.loads((fixtures / "clusters.json").read_text(encoding="utf-8"))["clusters"])
    print(f"SELECT order-dependence  reps={args.reps}/arm  arms={args.arms}  clusters={n_clusters}  seed={args.seed}")
    reps = asyncio.run(_run_all(args))
    summary = summarise(reps, n_clusters)
    out = Path(args.work) / "summary.json"
    out.write_text(json.dumps({"summary": _jsonable(summary), "reps": _jsonable(reps)}, indent=2), encoding="utf-8")
    _print_summary(summary)
    print(f"\n  wrote {out}")
    return 0


def _print_summary(summary: dict) -> None:
    print()
    for arm in (a for a in ARMS if a in summary):
        s = summary[arm]
        print(
            f"  {arm:8s} all Jaccard mean {s['all_jaccard_mean']:.3f} "
            f"[{s['all_jaccard_min']:.2f}-{s['all_jaccard_max']:.2f}]  "
            f"must_know {s['must_know_jaccard_mean']:.3f}  picks {s['picks_per_rep']}  "
            f"always {len(s['always_selected'])}  ever {s['ever_selected']}  "
            f"drifted {s['drifted']}/{s['stories']}  unresolved {s['unresolved']}  ${s['cost_usd']:.2f}"
        )
    for key, val in summary.items():
        if key.startswith("cross_"):
            print(f"  {key.replace('_all_jaccard_mean', '')} all Jaccard mean {val:.3f}")
    for pair, by_metric in summary["tests"].items():
        for metric, t in by_metric.items():
            print(
                f"  {pair:20s} {metric:9s} gap {t['gap']:+.3f} (two-sided p {t['gap_p_two_sided']:.3f}, "
                f"p<=0.05 needs {t['gap_threshold_p05']})  shift {t['shift']:+.3f} "
                f"(one-sided p {t['shift_p_one_sided']:.3f})  over {t['splits']} splits"
            )


if __name__ == "__main__":
    raise SystemExit(main())
