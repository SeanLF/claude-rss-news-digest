"""Pooled comparison across the HELD-OUT sets, per arm (model x thinking).

Sets have different known-bad counts (set 2 = 12 after adjudication, set 3 = 9), so
runs are pooled as RECALL FRACTIONS, not raw counts. Reports Welch t and a
Mann-Whitney U (normal approximation, tie-corrected) so a marginal gap is not dressed
up as significance by one test's assumptions.
"""
import json, glob, collections, math, statistics as st, unicodedata
from pathlib import Path
import importlib.util

spec = importlib.util.spec_from_file_location(
    "rescore_all", "/Users/sean/Developer/news-digest/scratch/coherence-models/rescore_all.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)

HELDOUT = {"planted278", "planted278b", "planted274"}


def welch(a, b):
    na, nb = len(a), len(b)
    ma, mb = st.mean(a), st.mean(b)
    va, vb = (st.variance(a) if na > 1 else 0.0), (st.variance(b) if nb > 1 else 0.0)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return float("inf") if ma != mb else 0.0, None
    t = (ma - mb) / se
    num = (va / na + vb / nb) ** 2
    den = ((va / na) ** 2 / (na - 1) if na > 1 else 0) + ((vb / nb) ** 2 / (nb - 1) if nb > 1 else 0)
    df = num / den if den else float("nan")
    return t, df


def mannwhitney(a, b):
    """U with tie-corrected normal approximation -> two-sided p."""
    comb = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks, i = {}, 0
    vals = [c[0] for c in comb]
    rk = [0.0] * len(comb)
    while i < len(comb):
        j = i
        while j + 1 < len(comb) and vals[j + 1] == vals[i]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rk[k] = r
        i = j + 1
    ra = sum(rk[i] for i in range(len(comb)) if comb[i][1] == 0)
    na, nb = len(a), len(b)
    ua = ra - na * (na + 1) / 2
    mu = na * nb / 2
    tie = 0
    cnt = collections.Counter(vals)
    for c in cnt.values():
        tie += c ** 3 - c
    n = na + nb
    sd = math.sqrt(na * nb / 12 * ((n + 1) - tie / (n * (n - 1)))) if n > 1 else 0
    if sd == 0:
        return ua, float("nan")
    z = (abs(ua - mu) - 0.5) / sd
    # Continuity correction can push the two-sided tail above 1 when the samples
    # barely differ; a probability cannot exceed 1, so clamp rather than print p>1.
    p = min(1.0, 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))
    return ua, p


def main():
    rows = []
    for f in sorted(glob.glob("/Users/sean/Developer/news-digest/scratch/coherence-models/out/*.jsonl")):
        for line in Path(f).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))

    per = collections.defaultdict(list)
    for r in rows:
        if r["set"] not in HELDOUT or not r.get("ok") or "report" not in r:
            continue
        hard, clean, border, n2i = R.labels_for(r["set"])
        fl, _ = R.flags_of(r["report"], n2i)
        arm = f"{r['model']}/{r.get('thinking')}"
        per[arm].append({"set": r["set"], "frac": len(fl & hard) / len(hard),
                         "fp": len(fl & clean), "cost": r["total_cost_usd"], "wall": r["wall_s"]})

    print("POOLED across held-out sets (recall as fraction of that set's known-bad count)\n")
    print(f"{'arm':<32}{'n':>3}  {'recall frac':<24}{'FP/run':<12}{'$/run':<10}{'sec/run'}")
    for a in sorted(per):
        v = per[a]; fr = [x["frac"] for x in v]
        print(f"{a:<32}{len(v):>3}  {st.mean(fr):.3f} ±{st.stdev(fr):.3f} [{min(fr):.2f}-{max(fr):.2f}]   "
              f"{st.mean([x['fp'] for x in v]):<12.2f}{st.mean([x['cost'] for x in v]):<10.3f}"
              f"{st.mean([x['wall'] for x in v]):.0f}")

    print("\nCONTRASTS (pooled held-out recall fraction)")
    def cmp(a, b):
        if a not in per or b not in per: return
        x = [z["frac"] for z in per[a]]; y = [z["frac"] for z in per[b]]
        t, df = welch(x, y); u, p = mannwhitney(x, y)
        d = st.mean(x) - st.mean(y)
        verdict = "SIGNIFICANT" if (p == p and p < 0.05) else "inside the spread"
        print(f"  {a}  vs  {b}\n      diff={d:+.3f}  Welch t={t:.2f} (df~{df:.1f})  MWU p={p:.3f}  -> {verdict}")
    cmp("claude-opus-5/disabled", "claude-sonnet-5/disabled")
    cmp("claude-opus-5/adaptive", "claude-sonnet-5/adaptive")
    cmp("claude-opus-5/adaptive", "claude-opus-5/disabled")
    cmp("claude-sonnet-5/adaptive", "claude-sonnet-5/disabled")
    cmp("claude-sonnet-5/adaptive", "claude-opus-5/disabled")


if __name__ == "__main__":
    main()
