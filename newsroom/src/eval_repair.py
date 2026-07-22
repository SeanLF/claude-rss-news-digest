"""Harness-faithful eval for the REPAIR stage (repair-not-drop).

Runs the real `.claude/agents/repair.md` through the same claude-agent-sdk path
production uses, against the frozen run-245 snapshot, and scores whether it fixed
the flagged specific WITHOUT gutting the field.

It synthesizes repair_requests.json from the labelled hard-positives (their
`why`/`claim` become the checker reason), runs the repairer, and scores each
repaired field with deterministic string assertions from labels.json's ``repair``
block: the flagged specific must be GONE (``must_not_contain``), a substitute must
carry a supported replacement (``must_contain_any``), and preservation is scored
CONDITIONAL on ``expected_action`` -- a delete/shrink is expected to get shorter,
so only a *substitute* answered by gutting the whole clause counts against it
(RARR's reward-hacking failure mode).

It then runs a scoped no-new-error re-check: the LIVE coherence.md (the prod
checker) over ONLY the repaired stories, unioned across ``--recheck-runs`` passes
(a story is clean only if the checker passes it on EVERY run). This is what the
string assertions cannot see -- a repair that removed the flagged specific but
left/added one the checker still catches (on a bare-headline source, even a
shrunk summary can assert more than the source supports). It is reported, not
gated in the middle (the checker is stochastic), but "the checker accepts NONE of
the repairs" is egregious -- repair would rescue nothing in prod.

Makes REAL model calls on the subscription -> opt-in only (``make eval-repair`` /
``bin/eval-repair``), never in CI. The stage is stochastic, so it reports a
per-run scorecard over N runs and exits non-zero only on an EGREGIOUS regression
(shape invalid, no error removed on any run, or a substitute gutted). It reads the
live repair.md, so it tests whatever the prompt currently says.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from eval_coherence import _norm, load_agent_for_eval, run_agent_to_file

AGENT = Path("/app/.claude/agents/repair.md")
FIXTURES = Path("/app/eval-fixtures")
REQUESTS_NAME = "repair_requests.json"
OUTPUT_NAME = "repaired_fields.json"
_TEXT_FIELDS = ("headline", "summary", "why_it_matters")

# A substitute that keeps under this fraction of the original field length has
# almost certainly deleted the whole clause instead of correcting the specific --
# RARR's "attribution up, preservation collapsed" reward-hack. delete/shrink are
# exempt (getting shorter is the correct action there).
_SUBSTITUTE_PRESERVATION_FLOOR = 0.5


def build_repair_requests(fixtures: Path, labels: dict) -> tuple[dict, dict[frozenset, int], dict[int, str]]:
    """Synthesize repair_requests.json from the labelled repairable hard-positives.

    Returns the requests payload, an article_ids->idx map (to score results back
    to a label), and an idx->original-flagged-field-text map (for preservation).
    Raises SystemExit if a labelled headline has no matching draft story (the
    fixture drifted), so the eval fails loudly rather than silently scoring nothing.
    """
    draft = json.loads((fixtures / "draft_selections.json").read_text(encoding="utf-8"))
    stories = {}
    for tier in ("must_know", "should_know"):
        for item in draft.get(tier, []):
            if isinstance(item, dict):
                stories[_norm(item.get("headline", ""))] = item

    hard_by_idx_field = {(hp["idx"], hp["field"]): hp for hp in labels["hard_positives"]}
    repair_labels = labels["repair"]
    idx_headlines = labels["idx_headlines"]

    requests = []
    idx_by_ids: dict[frozenset, int] = {}
    orig_by_idx: dict[int, str] = {}
    for idx_str, spec in repair_labels.items():
        idx = int(idx_str)
        field = spec["field"]
        headline = idx_headlines[idx_str]
        item = stories.get(_norm(headline))
        if item is None:
            raise SystemExit(f"repair label idx {idx}: no draft story matches headline {headline!r} (fixture drift)")
        article_ids = sorted(
            s["article_id"]
            for s in item.get("sources", [])
            if isinstance(s, dict) and isinstance(s.get("article_id"), str)
        )
        # Fail loud on a fixture that can't be scored: an empty or duplicate
        # article_ids set would collide in idx_by_ids and silently mis-score
        # (prod repair.py guards the same case). Distinct non-empty today; this
        # protects future hand-added repairable-fixture stories.
        key = frozenset(article_ids)
        if not key:
            raise SystemExit(f"repair label idx {idx}: story has no article_ids to key on (fixture drift)")
        if key in idx_by_ids:
            raise SystemExit(f"repair label idx {idx}: article_ids {article_ids} collide with idx {idx_by_ids[key]}")
        hp = hard_by_idx_field.get((idx, field), {})
        reason = f"{field}: {hp.get('why') or hp.get('claim') or 'flagged as unsupported'}"
        requests.append(
            {
                "article_ids": article_ids,
                "failed_fields": [field],
                "reason": reason,
                "fields": {f: item.get(f, "") for f in _TEXT_FIELDS},
            }
        )
        idx_by_ids[key] = idx
        orig_by_idx[idx] = item.get(field, "") or ""

    payload = {"requests": requests}
    (fixtures / REQUESTS_NAME).write_text(json.dumps(payload, indent=2))
    return payload, idx_by_ids, orig_by_idx


def score_repair(
    repaired_path: Path, labels: dict, idx_by_ids: dict[frozenset, int], orig_by_idx: dict[int, str]
) -> dict:
    """Score repaired_fields.json against the labelled repair expectations.

    Per repairable story: shape_ok (only the flagged field returned), error_removed
    (must_not_contain gone AND, for substitutes, a must_contain_any replacement
    present), and preservation_ok (a substitute kept enough of the original length;
    delete/shrink always pass). A labelled story with no repaired entry counts as
    error_NOT_removed (a silent miss), never a pass.
    """
    data = json.loads(repaired_path.read_text(encoding="utf-8"))
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"{repaired_path.name}: no 'results' list (broken run or schema drift)")

    repair_labels = labels["repair"]
    per_idx: dict[int, dict] = {}
    shape_errors: list[str] = []

    for r in results:
        if not isinstance(r, dict):
            shape_errors.append(f"non-object result: {r!r}")
            continue
        ids = frozenset(r.get("article_ids") or []) if isinstance(r.get("article_ids"), list) else frozenset()
        idx = idx_by_ids.get(ids)
        if idx is None:
            shape_errors.append(f"result with unknown article_ids {sorted(ids)}")
            continue
        spec = repair_labels[str(idx)]
        field = spec["field"]
        present = [f for f in _TEXT_FIELDS if f in r]
        shape_ok = present == [field]
        text = r.get(field) if isinstance(r.get(field), str) else ""
        text_l = text.lower()
        no_bad = all(s.lower() not in text_l for s in spec.get("must_not_contain", []))
        need_any = spec.get("must_contain_any") or []
        has_any = (not need_any) or any(s.lower() in text_l for s in need_any)
        error_removed = bool(text.strip()) and no_bad and has_any
        orig_len = max(1, len(orig_by_idx.get(idx, "")))
        ratio = len(text) / orig_len
        preservation_ok = spec["expected_action"] != "substitute" or ratio >= _SUBSTITUTE_PRESERVATION_FLOOR
        per_idx[idx] = {
            "shape_ok": shape_ok,
            "error_removed": error_removed,
            "preservation_ok": preservation_ok,
            "expected_action": spec["expected_action"],
            "ratio": round(ratio, 2),
        }

    labelled = {int(i) for i in repair_labels}
    missing = sorted(labelled - set(per_idx))
    return {
        "per_idx": per_idx,
        "shape_errors": shape_errors,
        "missing": missing,
        "n_labelled": len(labelled),
        "error_removed": sorted(i for i, v in per_idx.items() if v["error_removed"]),
        "shape_bad": sorted(i for i, v in per_idx.items() if not v["shape_ok"]),
        "gutted_substitutes": sorted(i for i, v in per_idx.items() if not v["preservation_ok"]),
    }


COHERENCE_AGENT = Path("/app/.claude/agents/coherence.md")
RECHECK_DRAFT_NAME = "recheck_draft.json"
RECHECK_REPORT_NAME = "recheck_report.json"


def build_recheck_draft(draft: dict, repaired_results: list) -> dict:
    """A draft_selections-shaped doc of the patched stories (the original story with
    the repaired field applied, matched by article_ids) for the scoped re-check --
    the SAME shape orchestrate._build_recheck_draft feeds the prod re-check."""
    by_ids: dict[frozenset, dict] = {}
    for tier in ("must_know", "should_know"):
        for item in draft.get(tier, []):
            if isinstance(item, dict):
                ids = frozenset(
                    s["article_id"]
                    for s in item.get("sources", [])
                    if isinstance(s, dict) and isinstance(s.get("article_id"), str)
                )
                if ids:
                    by_ids[ids] = item
    patched = []
    for r in repaired_results:
        if not isinstance(r, dict):
            continue
        item = by_ids.get(frozenset(r.get("article_ids") or []))
        if item is None:
            continue
        story = dict(item)
        for f in _TEXT_FIELDS:
            if f in r and isinstance(r[f], str):
                story[f] = r[f]
        patched.append(story)
    return {"must_know": patched, "should_know": [], "preheader": "recheck"}


def score_recheck(recheck_path: Path, idx_by_ids: dict[frozenset, int]) -> dict[int, bool]:
    """Which repaired stories the prod checker PASSED on the patched text, keyed by
    idx. A repaired story with no re-check entry is simply absent (the caller treats
    absence as not-confirmed -> fail-closed, matching prod's re-check)."""
    data = json.loads(recheck_path.read_text(encoding="utf-8"))
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"{recheck_path.name}: no 'results' list (broken run or schema drift)")
    passed: dict[int, bool] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        idx = idx_by_ids.get(frozenset(r.get("article_ids") or []))
        if idx is not None:
            passed[idx] = r.get("pass") is True
    return passed


def _recheck_agent(fixtures: Path, model_override: str | None) -> tuple[str, str, dict, list[str]]:
    """coherence.md redirected for the eval (prod path -> fixtures) AND re-pointed at
    the recheck files (draft_selections -> recheck_draft, coherence_report ->
    recheck_report), so the no-new-error check reuses the LIVE prod checker verbatim."""
    model, body, thinking, tools = load_agent_for_eval(COHERENCE_AGENT, fixtures, model_override)
    for src, dst in (("draft_selections.json", RECHECK_DRAFT_NAME), ("coherence_report.json", RECHECK_REPORT_NAME)):
        if src not in body:
            raise SystemExit(f"coherence.md: expected {src!r} to re-point for the repair re-check; prompt drifted")
        body = body.replace(src, dst)
    return model, body, thinking, tools


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=str(AGENT))
    ap.add_argument("--fixtures", default=str(FIXTURES))
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--model", default=None, help="override repair.md's frontmatter model")
    ap.add_argument(
        "--recheck-runs",
        type=int,
        default=1,
        help="scoped coherence.md re-checks over the repaired fields (0 = skip); union across runs (a story is clean only if it passes EVERY run)",
    )
    args = ap.parse_args()

    runs = max(1, args.runs)
    fixtures = Path(args.fixtures)
    labels = json.loads((fixtures / "labels.json").read_text(encoding="utf-8"))
    model, body, thinking, tools = load_agent_for_eval(Path(args.agent), fixtures, args.model)

    _, idx_by_ids, orig_by_idx = build_repair_requests(fixtures, labels)
    n = len(labels["repair"])
    print(f"REPAIR eval  model={model}  thinking={thinking['type']}  runs={runs}  fixtures={fixtures.name}")
    print(f"  {n} repairable stories: {sorted(int(i) for i in labels['repair'])}\n")

    scores = []
    for i in range(runs):
        asyncio.run(run_agent_to_file("repair", fixtures / OUTPUT_NAME, model, body, thinking, tools))
        s = score_repair(fixtures / OUTPUT_NAME, labels, idx_by_ids, orig_by_idx)
        scores.append(s)
        print(
            f"  run {i}: error-removed {len(s['error_removed'])}/{s['n_labelled']}  "
            f"shape-bad {len(s['shape_bad'])}  gutted-substitutes {len(s['gutted_substitutes'])}  "
            f"missing {len(s['missing'])}"
        )
        for idx in sorted(s["per_idx"]):
            v = s["per_idx"][idx]
            flag = "OK " if v["error_removed"] and v["shape_ok"] and v["preservation_ok"] else "XX "
            print(
                f"      {flag}idx {idx} [{v['expected_action']}] removed={v['error_removed']} shape={v['shape_ok']} pres={v['preservation_ok']} ratio={v['ratio']}"
            )
        if s["missing"]:
            print(f"          MISSING (no repaired entry): {s['missing']}")
        if s["shape_errors"]:
            print(f"          SHAPE ERRORS: {s['shape_errors']}")

    best_removed = max(len(s["error_removed"]) for s in scores)
    print(f"\n  best error-removed {best_removed}/{scores[0]['n_labelled']}")

    # No-new-error: run the LIVE coherence.md over the (last run's) repaired stories
    # and report how many the prod checker PASSES. This is what the string
    # assertions cannot see -- a repair that removed the flagged specific but added
    # a new one the checker catches. Reported, not gated: the checker is stochastic
    # and imperfect (a flag may be a residual it now catches OR a checker miss on the
    # original), so a human reads the middle -- same stance as eval_coherence.
    recheck_flagged: list[int] = []
    if args.recheck_runs > 0:
        draft = json.loads((fixtures / "draft_selections.json").read_text(encoding="utf-8"))
        repaired_results = json.loads((fixtures / OUTPUT_NAME).read_text(encoding="utf-8")).get("results", [])
        (fixtures / RECHECK_DRAFT_NAME).write_text(json.dumps(build_recheck_draft(draft, repaired_results), indent=2))
        cmodel, cbody, cthinking, ctools = _recheck_agent(fixtures, args.model)
        pass_counts: dict[int, int] = dict.fromkeys(idx_by_ids.values(), 0)
        for _ in range(args.recheck_runs):
            asyncio.run(
                run_agent_to_file("repair_recheck", fixtures / RECHECK_REPORT_NAME, cmodel, cbody, cthinking, ctools)
            )
            for idx, ok in score_recheck(fixtures / RECHECK_REPORT_NAME, idx_by_ids).items():
                if ok:
                    pass_counts[idx] += 1
        # Union of failures: clean only if the checker passed the patch on EVERY run.
        clean = sorted(i for i, c in pass_counts.items() if c == args.recheck_runs)
        recheck_flagged = sorted(set(idx_by_ids.values()) - set(clean))
        print(
            f"  no-new-error re-check (coherence.md x{args.recheck_runs}, union): {len(clean)}/{len(idx_by_ids)} pass"
        )
        if recheck_flagged:
            print(f"          re-check FLAGGED (residual/new error, or gutted): {recheck_flagged}")

    fail = []
    if any(s["shape_errors"] or s["shape_bad"] for s in scores):
        fail.append("a repaired entry had an invalid shape (wrong/extra field, or unknown article_ids)")
    if best_removed == 0:
        fail.append("no flagged error removed on any run (repairer is a no-op)")
    if any(s["gutted_substitutes"] for s in scores):
        fail.append("a substitute was answered by gutting the field (preservation collapse)")
    if args.recheck_runs > 0 and len(idx_by_ids) and not (set(idx_by_ids.values()) - set(recheck_flagged)):
        fail.append("the re-check accepted NONE of the repairs (repair rescues nothing in prod)")
    if fail:
        print("\n  REGRESSION: " + "; ".join(fail))
        return 1
    print("\n  OK (no egregious regression; review the scorecard for per-story removal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
