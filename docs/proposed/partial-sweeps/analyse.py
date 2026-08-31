"""Analyse the WRITE 2x2. Spread FIRST, then the comparison.

Primary metric: unsupported-specific rate from score_specifics.py, reported twice --
absent from the story's CITED sources, and absent from the ENTIRE run pool (the
prior-knowledge fabrication signal).

Only the low-false-positive specific classes (`ent`, `num`, `numword`) enter the
primary. `entphrase` is reported separately and marked noisy: its connector-glued
runs ("Bishkek for SCO") produce artifact misses that are not fabrications.

Between-arm claims are made ONLY against the widest within-arm spread, and p-values
come from a two-sided permutation test on the marginal means (no scipy dependency,
exact for these n).
"""
from __future__ import annotations

import itertools, json, math, random, statistics as st, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score_specifics import load_corpus, score_draft  # noqa: E402

ROOT = Path(__file__).parent
OUT = ROOT / "out"
PRIM = {"ent", "num", "numword"}
ARMS = [("claude-sonnet-4-6", "disabled"), ("claude-sonnet-4-6", "adaptive"),
        ("claude-sonnet-5", "adaptive"), ("claude-sonnet-5", "disabled")]
SHORT = {"claude-sonnet-4-6": "s4.6", "claude-sonnet-5": "s5"}


def perm_p(a: list[float], b: list[float], iters: int = 50_000) -> float:
    """Two-sided permutation test on the difference of means."""
    obs = abs(st.mean(a) - st.mean(b))
    pool = a + b
    na = len(a)
    # math.comb FIRST: materialising combinations(48, 24) is 1.6e13 tuples and hangs.
    n_combos = math.comb(len(pool), na)
    if n_combos <= iters:
        combos = list(itertools.combinations(range(len(pool)), na))
        hits = sum(1 for c in combos
                   if abs(st.mean([pool[i] for i in c])
                          - st.mean([pool[i] for i in range(len(pool)) if i not in set(c)])) >= obs - 1e-12)
        return hits / n_combos
    rng = random.Random(0)
    hits = 0
    for _ in range(iters):
        s = pool[:]; rng.shuffle(s)
        if abs(st.mean(s[:na]) - st.mean(s[na:])) >= obs - 1e-12:
            hits += 1
    return hits / iters


def spread(v: list[float]) -> str:
    if len(v) < 2:
        return f"n={len(v)}"
    return (f"n={len(v)} mean={st.mean(v):.4f} range={min(v):.4f}-{max(v):.4f} "
            f"spread={max(v)-min(v):.4f} sd={st.pstdev(v):.4f}")


def main() -> int:
    recs = []
    for f in sorted(OUT.glob("records_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                recs.append(json.loads(line))
    recs = [r for r in recs if r.get("ok") and r.get("draft_path") and r["rep"] < 900]

    corpora = {}
    for r in recs:
        rid = r["run_id"]
        if rid not in corpora:
            corpora[rid] = load_corpus(ROOT / "fixtures" / f"run{rid}" / "input")

    judge = defaultdict(list)
    for jf in sorted(OUT.glob("judge_*.jsonl")):
        for line in jf.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                if j.get("ok"):
                    judge[j["draft"]].append(j)

    per_rec = []
    for r in recs:
        per, pool = corpora[r["run_id"]]
        draft = json.loads((OUT / "drafts" / r["draft_path"]).read_text())
        sc = score_draft(draft, per, pool)
        rows = [x for x in sc["rows"] if x["kind"] in PRIM]
        phr = [x for x in sc["rows"] if x["kind"] == "entphrase"]
        stories = sc["stories"]
        u = r.get("usage") or {}
        d = dict(
            arm=f"{SHORT[r['model']]}/{r['thinking']}", model=r["model"], thinking=r["thinking"],
            run_id=r["run_id"], rep=r["rep"], draft=r["draft_path"],
            n_spec=len(rows),
            pool_abs=sum(1 for x in rows if not x["in_pool"]),
            cited_abs=sum(1 for x in rows if not x["in_cited"]),
            **{f"pool_abs_{f}": sum(1 for x in rows if x["field"] == f and not x["in_pool"])
               for f in ("headline", "summary", "why_it_matters")},
            **{f"cited_abs_{f}": sum(1 for x in rows if x["field"] == f and not x["in_cited"])
               for f in ("headline", "summary", "why_it_matters")},
            **{f"n_spec_{f}": sum(1 for x in rows if x["field"] == f)
               for f in ("headline", "summary", "why_it_matters")},
            phr_n=len(phr), phr_pool_abs=sum(1 for x in phr if not x["in_pool"]),
            n_stories=len(stories),
            summary_chars=st.mean([s["summary_chars"] for s in stories]) if stories else 0.0,
            headline_chars=st.mean([s["headline_chars"] for s in stories]) if stories else 0.0,
            why_chars=st.mean([s["why_chars"] for s in stories]) if stories else 0.0,
            why_empty=sum(s["why_empty"] for s in stories),
            n_sources=st.mean([s["n_sources"] for s in stories]) if stories else 0.0,
            cost=r.get("total_cost_usd") or 0.0, wall=r.get("wall_s") or 0.0,
            in_tok=u.get("input_tokens", 0), out_tok=u.get("output_tokens", 0),
            cache_read=u.get("cache_read_input_tokens", 0),
            cache_create=u.get("cache_creation_input_tokens", 0),
            think_tok=(u.get("output_tokens_details") or {}).get("thinking_tokens"),
        )
        d["pool_rate"] = d["pool_abs"] / d["n_spec"] if d["n_spec"] else 0.0
        d["cited_rate"] = d["cited_abs"] / d["n_spec"] if d["n_spec"] else 0.0
        d["pool_per_story"] = d["pool_abs"] / d["n_stories"] if d["n_stories"] else 0.0
        d["cited_per_story"] = d["cited_abs"] / d["n_stories"] if d["n_stories"] else 0.0
        js = judge.get(r["draft_path"], [])
        if js:
            d["judge_fail"] = st.mean([j["n_fail"] for j in js])
            d["judge_n"] = st.mean([j["n_results"] for j in js])
            d["judge_rate"] = st.mean([j["n_fail"] / j["n_results"] for j in js if j["n_results"]])
            d["judge_cost"] = st.mean([j.get("judge_cost_usd") or 0 for j in js])
            d["judge_why_only"] = st.mean([
                sum(1 for f in j["fails"] if (f.get("failed_fields") or []) == ["why_it_matters"])
                for j in js])
        per_rec.append(d)

    (OUT / "per_record.json").write_text(json.dumps(per_rec, indent=1))

    def by(key, filt=None):
        g = defaultdict(list)
        for d in per_rec:
            if filt and not filt(d):
                continue
            g[d["arm"]].append(d[key])
        return g

    print("=" * 100)
    print("MANIPULATION CHECK  (thinking_tokens must be 0 on disabled, >0 on adaptive)")
    for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
        v = [d["think_tok"] for d in per_rec if d["arm"] == arm]
        if v:
            print(f"  {arm:20s} n={len(v):2d} thinking_tokens min={min(x or 0 for x in v)} max={max(x or 0 for x in v)}")

    for metric, label in (("pool_rate", "POOL-ABSENT rate (prior-knowledge fabrication)"),
                          ("cited_rate", "CITED-ABSENT rate"),
                          ("pool_per_story", "POOL-ABSENT specifics per story"),
                          ("cited_per_story", "CITED-ABSENT specifics per story"),
                          ("summary_chars", "mean summary chars"),
                          ("judge_rate", "COHERENCE fail rate (MODEL-GRADED)")):
        print()
        print("=" * 100)
        print(f"### {label}")
        for rid in sorted({d["run_id"] for d in per_rec}):
            print(f"  -- source run {rid} --")
            for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
                v = [d[metric] for d in per_rec if d["arm"] == arm and d["run_id"] == rid and metric in d]
                if v:
                    print(f"    {arm:20s} {spread(v)}")
        print("  -- pooled --")
        arms_v = {}
        for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
            v = [d[metric] for d in per_rec if d["arm"] == arm and metric in d]
            arms_v[arm] = v
            if v:
                print(f"    {arm:20s} {spread(v)}")
        sp = [(max(v) - min(v)) for v in arms_v.values() if len(v) > 1]
        if not sp:
            continue
        widest = max(sp)
        print(f"    widest within-arm spread = {widest:.4f}")
        # Marginal effects on RUN-CENTRED values: the two source runs differ in
        # baseline (different articles), so pooling raw values lets an arm that
        # happens to have more reps from one run carry that run's level into the
        # marginal mean. Centring each record by its own run's grand mean removes
        # the run main effect and leaves the arm contrast.
        run_mean = {}
        for rid in {d["run_id"] for d in per_rec}:
            v = [d[metric] for d in per_rec if d["run_id"] == rid and metric in d]
            if v:
                run_mean[rid] = st.mean(v)
        cen = [dict(d, _c=d[metric] - run_mean[d["run_id"]]) for d in per_rec
               if metric in d and d["run_id"] in run_mean]
        mm = {m: [d["_c"] for d in cen if d["model"] == m] for m in {a[0] for a in ARMS}}
        tt = {t: [d["_c"] for d in cen if d["thinking"] == t] for t in ("disabled", "adaptive")}
        print("    (marginal effects below are RUN-CENTRED)")
        if all(len(v) > 1 for v in mm.values()):
            a, b = mm["claude-sonnet-4-6"], mm["claude-sonnet-5"]
            print(f"    MODEL   effect s5 - s4.6      = {st.mean(b)-st.mean(a):+.4f}  p={perm_p(a,b):.4f}")
        if all(len(v) > 1 for v in tt.values()):
            a, b = tt["disabled"], tt["adaptive"]
            print(f"    THINKING effect adaptive - dis = {st.mean(b)-st.mean(a):+.4f}  p={perm_p(a,b):.4f}")
        cen_arm = {arm: [d["_c"] for d in cen if d["arm"] == arm]
                   for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]}
        for arm, v in cen_arm.items():
            if len(v) > 1 and arm != "s4.6/disabled" and len(cen_arm["s4.6/disabled"]) > 1:
                c = cen_arm["s4.6/disabled"]
                gap = st.mean(v) - st.mean(c)
                verdict = "INSIDE noise" if abs(gap) < widest else "exceeds widest spread"
                print(f"    vs control {arm:16s} gap={gap:+.4f} ({verdict}) p={perm_p(c, v):.4f}")

    print()
    print("=" * 100)
    print("### COST / LATENCY / TOKENS (per WRITE call)")
    print(f"  {'arm':20s} {'n':>3} {'$/call':>8} {'wall_s':>8} {'in_tok':>9} {'out_tok':>9} {'think':>8} {'cache_rd':>10} {'cache_cr':>10} {'stories':>8}")
    for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
        g = [d for d in per_rec if d["arm"] == arm]
        if not g:
            continue
        f = lambda k: st.mean([d[k] or 0 for d in g])
        print(f"  {arm:20s} {len(g):3d} {f('cost'):8.4f} {f('wall'):8.1f} {f('in_tok'):9.0f} "
              f"{f('out_tok'):9.0f} {f('think_tok'):8.0f} {f('cache_read'):10.0f} {f('cache_create'):10.0f} {f('n_stories'):8.1f}")

    print()
    print("### WHERE THE UNSUPPORTED SPECIFICS SIT (mean per draft, by field)")
    print(f"  {'arm':20s} | {'pool-absent':^30s} | {'cited-absent':^30s}")
    print(f"  {'':20s} | {'headline':>9} {'summary':>9} {'why':>9} | {'headline':>9} {'summary':>9} {'why':>9}")
    for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
        g = [d for d in per_rec if d["arm"] == arm]
        if not g:
            continue
        f = lambda k: st.mean([d.get(k) or 0 for d in g])
        print(f"  {arm:20s} | {f('pool_abs_headline'):9.2f} {f('pool_abs_summary'):9.2f} {f('pool_abs_why_it_matters'):9.2f}"
              f" | {f('cited_abs_headline'):9.2f} {f('cited_abs_summary'):9.2f} {f('cited_abs_why_it_matters'):9.2f}")

    print()
    print("### LENGTH / SHAPE")
    print(f"  {'arm':20s} {'summary_ch':>11} {'headline_ch':>12} {'why_ch':>8} {'why_empty':>10} {'srcs/story':>11} {'specifics':>10} {'phr_abs':>8}")
    for arm in [f"{SHORT[m]}/{t}" for m, t in ARMS]:
        g = [d for d in per_rec if d["arm"] == arm]
        if not g:
            continue
        f = lambda k: st.mean([d[k] or 0 for d in g])
        print(f"  {arm:20s} {f('summary_chars'):11.1f} {f('headline_chars'):12.1f} {f('why_chars'):8.1f} "
              f"{f('why_empty'):10.2f} {f('n_sources'):11.1f} {f('n_spec'):10.1f} {f('phr_pool_abs'):8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
