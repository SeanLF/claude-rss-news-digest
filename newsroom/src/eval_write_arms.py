"""Replay a past run through the real WRITE stage under two arms, N reps each.

Built to answer "does this input/prompt change actually do anything", which this
project has repeatedly answered wrong by running one arm once. It exists because
the measured effect of a WRITE change has twice been smaller than the run-to-run
variance of a single unchanged configuration.

**The primary output is the WITHIN-ARM SPREAD, not the between-arm difference.**
Read the noise floor first. If the arms differ by less than the spread of either
one, the experiment says nothing, however tidy the means look. On the run-237
Burnham case the control alone ranged 0.375-0.810 on byte-identical inputs, which
retroactively voided every single-run comparison made on that endpoint.

Arms:
  A  the run's archived inputs plus recent_digest_headlines.txt (production today)
  B  A plus thread_deltas.txt -- the linker-matched threads' whats_new facts --
     and one prompt line pointing WRITE at it

Makes REAL model calls on the subscription: opt-in, never in CI. Roughly one
WRITE stage per rep per arm.

A caution the corpus choice earns: the same headline pair scores 0.869 against a
prior-week IDF corpus and 0.661 against an all-time one. A fixed similarity
threshold is not portable between them, so this reports both.

Usage:
  bin/eval-write-arms [--run 237] [--prior-run 236] [--story burnham] [--reps 4]
"""

import argparse
import asyncio
import json
import math
import re
import shutil
import sqlite3
import statistics as stats
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/app/src")

import orchestrate

DB = Path("/app/data/digest.db")
WORK = Path("/app/data/eval-write-arms")
INPUT_NAMES = ("selected.json", "article_index.json")

_WORD = re.compile(r"[a-z0-9']+")


def _tok(s):
    return [w for w in _WORD.findall(s.lower()) if len(w) > 2]


def _idf(corpus):
    n = len(corpus)
    df = Counter()
    for doc in corpus:
        for w in set(_tok(doc)):
            df[w] += 1
    return {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}


def _vec(s, idf):
    tf = Counter(_tok(s))
    v = {w: (1 + math.log(c)) * idf.get(w, math.log(2.0)) for w, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def cos(a, b, idf):
    va, vb = _vec(a, idf), _vec(b, idf)
    return sum(x * vb.get(w, 0.0) for w, x in va.items())


def artifacts(conn, run):
    rows = conn.execute("SELECT artifact_name, content FROM run_artifacts WHERE run_id=?", (run,)).fetchall()
    keep = {n: c for n, c in rows if n in INPUT_NAMES or (n.startswith("articles_") and n.endswith(".csv"))}
    if "selected.json" not in keep:
        raise SystemExit(f"run {run} has no archived selected.json -- nothing to replay")
    return keep


def prior_headlines(conn, run, days=7):
    return conn.execute(
        """
        SELECT DISTINCT date(dr.run_at), sn.tier, sn.headline
        FROM shown_narratives sn JOIN digest_runs dr ON dr.id = sn.run_id
        WHERE dr.run_at < (SELECT run_at FROM digest_runs WHERE id = :r)
          AND dr.run_at >= datetime((SELECT run_at FROM digest_runs WHERE id = :r), :w)
        ORDER BY dr.run_at DESC
        """,
        {"r": run, "w": f"-{days} days"},
    ).fetchall()


def deltas(conn, run):
    """cluster_index -> whats_new facts for the run's linker-matched threads."""
    row = conn.execute(
        "SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='clusters.json'", (run,)
    ).fetchone()
    if not row:
        return {}
    idx_of = {c["story"]: i for i, c in enumerate(json.loads(row[0])["clusters"])}
    out = {}
    for story, content in conn.execute(
        "SELECT cluster_story, content FROM thread_installments WHERE run_id=? AND matched_score IS NOT NULL", (run,)
    ):
        parsed = json.loads(content) if content else {}
        facts = [f["fact"] for f in (parsed.get("whats_new") or []) if isinstance(f, dict) and f.get("fact")]
        if (i := idx_of.get(story)) is not None and facts:
            out[i] = facts
    return out


def build_arm(arm, arts, heads, delta_map):
    d = WORK / arm
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for name, content in arts.items():
        (d / name).write_text(content)
    (d / "recent_digest_headlines.txt").write_text("".join(f"{a} | {b}: {c}\n" for a, b, c in heads))
    if arm == "B":
        out = ["# Ongoing stories: what developed since the headline readers already saw.", ""]
        for i, facts in sorted(delta_map.items()):
            out += [f"cluster_index: {i}", "new_since:", *[f"  - {f}" for f in facts], ""]
        (d / "thread_deltas.txt").write_text("\n".join(out))
    return d


_B_READ = (
    "   - `{d}/thread_deltas.txt` (if it exists -- skip if not found). For any story whose\n"
    "     cluster_index appears there, `new_since` lists what developed since the headline readers\n"
    "     were already shown. Lead that story's headline with one of those developments.\n"
)


def build_specs(work_a, work_b):
    base = orchestrate.parse_agent_spec(Path("/app/.claude/agents/write.md"))

    def body_for(d, inject):
        body = base.body.replace("/app/data/claude_input", str(d))
        if inject:
            anchor = f"   - `{d}/recent_digest_headlines.txt` (if it exists -- skip if not found)\n"
            body = body.replace(anchor, anchor + _B_READ.format(d=d))
        return orchestrate.AgentSpec(name="write", model=base.model, tools_str=base.tools_str, body=body)

    a, b = body_for(work_a, False), body_for(work_b, True)
    if "thread_deltas.txt" not in b.body or "thread_deltas.txt" in a.body:
        raise SystemExit("arm construction failed -- prompt anchor did not match write.md")
    return a, b


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=237)
    ap.add_argument("--prior-run", type=int, default=236)
    ap.add_argument("--story", default="burnham", help="substring identifying the tracked headline")
    ap.add_argument("--reps", type=int, default=4)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    arts, heads = artifacts(conn, args.run), prior_headlines(conn, args.run)
    row = conn.execute(
        "SELECT headline FROM shown_narratives WHERE run_id=? AND lower(headline) LIKE ? LIMIT 1",
        (args.prior_run, f"%{args.story.lower()}%"),
    ).fetchone()
    if not row:
        raise SystemExit(f"no headline matching {args.story!r} in run {args.prior_run}")
    prior = row[0]

    week_idf = _idf([h for _, _, h in heads] + [prior])
    all_idf = _idf([r[0] for r in conn.execute("SELECT DISTINCT headline FROM shown_narratives")])

    work_a = build_arm("A", arts, heads, {})
    work_b = build_arm("B", arts, heads, deltas(conn, args.run))
    spec_a, spec_b = build_specs(work_a, work_b)
    print(f"prior ({args.prior_run}): {prior}\n")

    results = {}
    for arm, spec, work in (("A", spec_a, work_a), ("B", spec_b, work_b)):
        results[arm] = []
        for rep in range(1, args.reps + 1):
            out = work / "draft_selections.json"
            out.unlink(missing_ok=True)
            try:
                await orchestrate.run_stage(
                    spec,
                    label="write",
                    output_path=out,
                    validate=orchestrate.validate_draft,
                    model_override=None,
                    cwd=None,
                    claude_input_dir=work,
                )
            except Exception as e:
                print(f"[{arm}{rep}] FAILED {type(e).__name__}: {e}")
                continue
            payload = json.loads(out.read_text())
            (WORK / f"draft_{arm}{rep}.json").write_text(json.dumps(payload, indent=1))
            stories = (payload.get("must_know") or []) + (payload.get("should_know") or [])
            hit = next((s for s in stories if args.story.lower() in (s.get("headline") or "").lower()), None)
            if not hit:
                print(f"[{arm}{rep}] story {args.story!r} not present in output")
                continue
            h = hit["headline"]
            rec = {"rep": rep, "headline": h, "week": cos(h, prior, week_idf), "all": cos(h, prior, all_idf)}
            results[arm].append(rec)
            print(f"[{arm}{rep}] week={rec['week']:.3f} all={rec['all']:.3f}  {h}")

    (WORK / "results.json").write_text(json.dumps(results, indent=1))
    print("\n=== NOISE FLOOR (read this before the comparison) ===")
    for arm, recs in results.items():
        v = [r["week"] for r in recs]
        if len(v) > 1:
            print(
                f"  arm {arm}: n={len(v)} range={min(v):.3f}-{max(v):.3f} spread={max(v) - min(v):.3f} sd={stats.pstdev(v):.3f}"
            )
    a = [r["week"] for r in results.get("A", [])]
    b = [r["week"] for r in results.get("B", [])]
    if len(a) > 1 and len(b) > 1:
        gap = abs(stats.mean(a) - stats.mean(b))
        worst = max(max(a) - min(a), max(b) - min(b))
        print(f"\n  between-arm gap {gap:.3f} vs widest within-arm spread {worst:.3f}")
        print("  -> " + ("INCONCLUSIVE: gap is inside the noise" if gap < worst else "gap exceeds within-arm spread"))


if __name__ == "__main__":
    asyncio.run(main())
