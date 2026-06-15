"""Per-stage L1 eval layer: deterministically grade EACH subagent's output.

Where ``eval_graders`` grades the final assembled ``selections.json`` and
``eval_judge`` runs the L2 LLM-as-judge harness, this module grades the
*individual* stage artifacts each subagent writes -- CLUSTER, RECAP, SELECT,
WRITE, COHERENCE -- on RECORDED artifacts. Pure code + fixtures, no model
calls. It catches a regression at the stage that produced it rather than only
seeing the blended end-of-pipeline output.

Philosophy mirrors ``eval_graders``: binary pass/fail checks only (never a
Likert), cheap enough to run on every change. We reuse ``Check`` /
``GradeReport`` / ``GraderLimits`` / ``_word_count`` from that module so the
two layers report identically.

Stage artifact shapes (verified against recorded run-195 artifacts)::

    clusters.json     {"clusters": [{"story": "<label>", "article_ids": ["A1", ...]}, ...]}
    article_index.json  {"A1": {url, source_id, bias, original_title, name}, ...}
    recap.txt         plain text: a 2-3 sentence weekly thematic summary
    selected.json     {"must_know":   [{"cluster_index": int, "article_ids": [...]}, ...],
                       "should_know": [ ...same shape... ],
                       "not_covered_blurb": "..."}
    draft_selections.json
                      {"must_know":   [{headline, summary, why_it_matters,
                                        sources:[{article_id}], reporting_varies?}, ...],
                       "should_know": [ ...same shape... ],
                       "preheader": "<= 150 chars"}
    coherence_report.json
                      {"results": [{"headline", "article_ids", "pass", "reason"}, ...]}

Standalone: this module does not touch the live pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval_graders import GradeReport, GraderLimits, _word_count

# Stages whose WRITE/coherence shape carries the full article fields.
ARTICLE_TIERS = ("must_know", "should_know")

# Sane upper bound for the recap blurb (2-3 sentences). Generous so it does not
# fail spuriously, but tight enough to catch a runaway dump.
RECAP_MAX_CHARS = 600

# Substrings that mark a recap stub / error fallback rather than a real summary.
# The RECAP subagent emits these when its input (recent_rss_titles.csv) is
# missing -- a real regression we want the floor to catch, not pass silently.
_RECAP_STUB_MARKERS = (
    "could not be found",
    "no thematic summary",
    "no recent context",
    "limited recent context",
    "error",
    "unable to",
    "not available",
)


# --------------------------------------------------------------------------- #
# Internal helpers.
# --------------------------------------------------------------------------- #


def _iter_tier_items(payload: dict) -> list[tuple[str, dict]]:
    """Yield (tier, item) for every must_know/should_know entry that is a dict."""
    out: list[tuple[str, dict]] = []
    for tier in ARTICLE_TIERS:
        for item in payload.get(tier, []) or []:
            if isinstance(item, dict):
                out.append((tier, item))
    return out


def _nonempty_str(val: object) -> bool:
    return isinstance(val, str) and bool(val.strip())


# --------------------------------------------------------------------------- #
# CLUSTER.
# --------------------------------------------------------------------------- #


def grade_cluster(clusters: dict, article_index: dict) -> GradeReport:
    """Grade the CLUSTER subagent's clusters.json against article_index.json.

    Checks: non-empty cluster list; every cluster has a non-empty ``story`` and
    >=1 article_id; every referenced article_id exists in the index; no
    article_id is assigned to more than one cluster.
    """
    report = GradeReport()
    cluster_list = clusters.get("clusters") if isinstance(clusters, dict) else None

    if not isinstance(cluster_list, list) or not cluster_list:
        report.add("clusters_present", passed=False, detail="clusters list missing or empty")
        return report
    report.add("clusters_present", passed=True, detail=f"{len(cluster_list)} cluster(s)")

    # Each cluster has a non-empty story label.
    storyless = [i for i, c in enumerate(cluster_list) if not (isinstance(c, dict) and _nonempty_str(c.get("story")))]
    report.add(
        "cluster_story_nonempty",
        passed=not storyless,
        detail="ok" if not storyless else f"{len(storyless)} cluster(s) w/o story: idx {storyless[:8]}",
    )

    # Each cluster has >= 1 article_id.
    emptyish = [
        i
        for i, c in enumerate(cluster_list)
        if not (isinstance(c, dict) and isinstance(c.get("article_ids"), list) and c["article_ids"])
    ]
    report.add(
        "cluster_has_articles",
        passed=not emptyish,
        detail="ok" if not emptyish else f"{len(emptyish)} empty cluster(s): idx {emptyish[:8]}",
    )

    # Every article_id resolves in the index; track assignment counts for dedup.
    assignment: dict[str, int] = {}
    unknown: list[str] = []
    for c in cluster_list:
        if not isinstance(c, dict):
            continue
        for aid in c.get("article_ids", []) or []:
            assignment[aid] = assignment.get(aid, 0) + 1
            if aid not in article_index:
                unknown.append(aid)
    report.add(
        "cluster_ids_in_index",
        passed=not unknown,
        detail=f"ok ({len(assignment)} unique ids)" if not unknown else f"{len(unknown)} unknown: {unknown[:8]}",
    )

    # No article_id assigned to more than one cluster.
    dupes = sorted(aid for aid, n in assignment.items() if n > 1)
    report.add(
        "cluster_no_duplicate_assignment",
        passed=not dupes,
        detail="ok" if not dupes else f"{len(dupes)} id(s) in >1 cluster: {dupes[:8]}",
    )

    return report


# --------------------------------------------------------------------------- #
# RECAP.
# --------------------------------------------------------------------------- #


def grade_recap(recap_text: str) -> GradeReport:
    """Grade the RECAP subagent's recap.txt.

    Checks: non-empty; within a sane length bound; not a stub/error fallback.
    """
    report = GradeReport()
    text = recap_text if isinstance(recap_text, str) else ""
    stripped = text.strip()

    report.add("recap_nonempty", passed=bool(stripped), detail="ok" if stripped else "empty recap")

    n = len(stripped)
    report.add(
        "recap_length",
        passed=n <= RECAP_MAX_CHARS,
        detail=f"{n} chars (cap {RECAP_MAX_CHARS})",
    )

    lowered = stripped.lower()
    hit = next((m for m in _RECAP_STUB_MARKERS if m in lowered), None)
    report.add(
        "recap_not_stub",
        passed=hit is None,
        detail="ok" if hit is None else f"stub/error marker: {hit!r}",
    )

    return report


# --------------------------------------------------------------------------- #
# SELECT.
# --------------------------------------------------------------------------- #


def grade_select(selected: dict, clusters: dict, limits: GraderLimits | None = None) -> GradeReport:
    """Grade the SELECT subagent's selected.json against clusters.json.

    Checks: must_know/should_know present as lists; counts within
    GraderLimits ranges; every referenced cluster_index resolves to a real
    cluster; the article_ids on each selection are a subset of that cluster's
    article_ids.
    """
    limits = limits or GraderLimits()
    report = GradeReport()
    cluster_list = clusters.get("clusters") if isinstance(clusters, dict) else None
    cluster_list = cluster_list if isinstance(cluster_list, list) else []

    # Tiers present and typed.
    missing = [t for t in ARTICLE_TIERS if not isinstance(selected.get(t), list)]
    report.add(
        "select_tiers_present",
        passed=not missing,
        detail="ok" if not missing else f"missing/not-list: {missing}",
    )

    # Counts in range.
    counts = {
        "must_know": (len(selected.get("must_know") or []), limits.must_know_range),
        "should_know": (len(selected.get("should_know") or []), limits.should_know_range),
    }
    out_of_range = [f"{t}={n} not in [{lo},{hi}]" for t, (n, (lo, hi)) in counts.items() if not (lo <= n <= hi)]
    summary = ", ".join(f"{t}={n}" for t, (n, _) in counts.items())
    report.add(
        "select_counts_in_range",
        passed=not out_of_range,
        detail=summary if not out_of_range else "; ".join(out_of_range),
    )

    # cluster_index references resolve, and article_ids subset the cluster.
    bad_index: list[str] = []
    bad_subset: list[str] = []
    for tier, item in _iter_tier_items(selected):
        idx = item.get("cluster_index")
        if not isinstance(idx, int) or not (0 <= idx < len(cluster_list)):
            bad_index.append(f"{tier} cluster_index={idx!r}")
            continue
        cluster_ids = set(cluster_list[idx].get("article_ids", []) or [])
        sel_ids = set(item.get("article_ids", []) or [])
        stray = sel_ids - cluster_ids
        if stray:
            bad_subset.append(f"{tier}[idx {idx}] stray {sorted(stray)[:5]}")
    report.add(
        "select_cluster_index_resolves",
        passed=not bad_index,
        detail="ok" if not bad_index else f"{len(bad_index)} bad: " + "; ".join(bad_index[:5]),
    )
    report.add(
        "select_article_ids_in_cluster",
        passed=not bad_subset,
        detail="ok" if not bad_subset else f"{len(bad_subset)} mismatch: " + "; ".join(bad_subset[:5]),
    )

    return report


# --------------------------------------------------------------------------- #
# WRITE.
# --------------------------------------------------------------------------- #


def grade_write(draft: dict, article_index: dict, limits: GraderLimits | None = None) -> GradeReport:
    """Grade the WRITE subagent's draft_selections.json against article_index.json.

    Checks: each story has non-empty headline/summary/why_it_matters and >=1
    source; every source article_id resolves in the index; word caps respected
    (headline/summary/why); preheader present and <= cap chars.
    """
    limits = limits or GraderLimits()
    report = GradeReport()

    items = _iter_tier_items(draft)
    report.add(
        "write_items_present",
        passed=bool(items),
        detail=f"{len(items)} item(s)" if items else "no must_know/should_know items",
    )

    # Non-empty text fields.
    empties: list[str] = []
    for tier, item in items:
        for fld in ("headline", "summary", "why_it_matters"):
            if not _nonempty_str(item.get(fld)):
                empties.append(f"{tier}.{fld}")
    report.add(
        "write_text_fields_nonempty",
        passed=not empties,
        detail="ok" if not empties else f"{len(empties)} empty: {empties[:8]}",
    )

    # >= 1 source per item, and each source resolves in the index.
    sourceless: list[str] = []
    unknown_src: list[str] = []
    for tier, item in items:
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            sourceless.append(f"{tier}: {(item.get('headline') or '')[:40]!r}")
            continue
        for s in sources:
            aid = s.get("article_id") if isinstance(s, dict) else None
            if aid not in article_index:
                unknown_src.append(f"{aid!r}")
    report.add(
        "write_sources_nonempty",
        passed=not sourceless,
        detail="ok" if not sourceless else f"{len(sourceless)} sourceless: " + " | ".join(sourceless[:5]),
    )
    report.add(
        "write_source_ids_in_index",
        passed=not unknown_src,
        detail="ok" if not unknown_src else f"{len(unknown_src)} unknown src id(s): {unknown_src[:8]}",
    )

    # Word caps.
    for name, fld, cap in (
        ("write_headline_words", "headline", limits.headline_max_words),
        ("write_summary_words", "summary", limits.summary_max_words),
        ("write_why_words", "why_it_matters", limits.why_it_matters_max_words),
    ):
        over: list[str] = []
        for tier, item in items:
            val = item.get(fld)
            if isinstance(val, str):
                wc = _word_count(val)
                if wc > cap:
                    over.append(f"{tier} ({wc}w>{cap}): {(item.get('headline') or '')[:40]!r}")
        report.add(
            name,
            passed=not over,
            detail=f"ok (cap {cap}w)" if not over else f"{len(over)} over: " + " | ".join(over[:4]),
        )

    # Preheader present and within cap.
    preheader = draft.get("preheader")
    if not _nonempty_str(preheader):
        report.add("write_preheader_present", passed=False, detail="preheader missing or empty")
    else:
        report.add("write_preheader_present", passed=True, detail="ok")
        n = len(preheader)
        report.add(
            "write_preheader_length",
            passed=n <= limits.preheader_max_chars,
            detail=f"{n} chars (cap {limits.preheader_max_chars})",
        )

    return report


# --------------------------------------------------------------------------- #
# COHERENCE.
# --------------------------------------------------------------------------- #


def grade_coherence(coherence_report: dict, draft: dict) -> GradeReport:
    """Grade the COHERENCE subagent's coherence_report.json against the draft.

    Checks: results present as a list; every draft headline (must + should) has
    a verdict entry; each entry's ``pass`` is a real bool.
    """
    report = GradeReport()
    results = coherence_report.get("results") if isinstance(coherence_report, dict) else None

    if not isinstance(results, list) or not results:
        report.add("coherence_results_present", passed=False, detail="results list missing or empty")
        return report
    report.add("coherence_results_present", passed=True, detail=f"{len(results)} verdict(s)")

    verdict_heads = {r.get("headline") for r in results if isinstance(r, dict)}
    draft_heads = [item.get("headline") for _, item in _iter_tier_items(draft) if _nonempty_str(item.get("headline"))]
    uncovered = [h for h in draft_heads if h not in verdict_heads]
    report.add(
        "coherence_covers_all_headlines",
        passed=not uncovered,
        detail=f"ok ({len(draft_heads)} headlines)"
        if not uncovered
        else f"{len(uncovered)} headline(s) w/o verdict: " + " | ".join(h[:40] for h in uncovered[:4]),
    )

    non_bool = [
        (r.get("headline") or "")[:40] for r in results if isinstance(r, dict) and not isinstance(r.get("pass"), bool)
    ]
    report.add(
        "coherence_pass_is_bool",
        passed=not non_bool,
        detail="ok" if not non_bool else f"{len(non_bool)} non-bool pass: {non_bool[:4]}",
    )

    return report


# --------------------------------------------------------------------------- #
# Top-level orchestration.
# --------------------------------------------------------------------------- #

# Artifact filenames each grader consumes.
STAGE_ARTIFACTS = (
    "article_index.json",
    "clusters.json",
    "recap.txt",
    "selected.json",
    "draft_selections.json",
    "coherence_report.json",
)


def grade_all_stages(stage_dicts: dict, limits: GraderLimits | None = None) -> dict[str, GradeReport]:
    """Grade every stage from a dict of parsed artifacts.

    Args:
        stage_dicts: parsed artifacts keyed by artifact name (see
            ``STAGE_ARTIFACTS``); ``recap.txt`` is the raw string, the rest are
            parsed JSON.
        limits: caps/ranges; defaults to ``GraderLimits()``.

    Returns:
        ``{stage_name: GradeReport}`` for CLUSTER, RECAP, SELECT, WRITE,
        COHERENCE.
    """
    limits = limits or GraderLimits()
    article_index = stage_dicts.get("article_index.json", {})
    clusters = stage_dicts.get("clusters.json", {})
    draft = stage_dicts.get("draft_selections.json", {})
    return {
        "CLUSTER": grade_cluster(clusters, article_index),
        "RECAP": grade_recap(stage_dicts.get("recap.txt", "")),
        "SELECT": grade_select(stage_dicts.get("selected.json", {}), clusters, limits),
        "WRITE": grade_write(draft, article_index, limits),
        "COHERENCE": grade_coherence(stage_dicts.get("coherence_report.json", {}), draft),
    }


# --------------------------------------------------------------------------- #
# Loaders.
# --------------------------------------------------------------------------- #


class MissingArtifactError(RuntimeError):
    """An expected stage artifact was absent from the source (DB or directory)."""


def _parse_artifact(name: str, content: str) -> object:
    """Parse one artifact's raw content -- JSON for .json, raw text otherwise."""
    if name.endswith(".json"):
        return json.loads(content)
    return content


def load_stage_artifacts_from_db(run_id: int, db_path: str | Path) -> dict:
    """Read and parse the stage artifacts for ``run_id`` from ``run_artifacts``.

    Fails loudly (``MissingArtifactError``) if any artifact in
    ``STAGE_ARTIFACTS`` is absent for the run -- a partial set means the run
    crashed mid-pipeline and grading would be silently incomplete.
    """
    import sqlite3

    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT artifact_name, content FROM run_artifacts WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    by_name = dict(rows)
    missing = [n for n in STAGE_ARTIFACTS if n not in by_name]
    if missing:
        raise MissingArtifactError(
            f"run {run_id} is missing stage artifact(s): {', '.join(missing)} "
            f"(found: {', '.join(sorted(by_name)) or 'none'})"
        )
    return {n: _parse_artifact(n, by_name[n]) for n in STAGE_ARTIFACTS}


def load_stage_artifacts_from_dir(directory: str | Path) -> dict:
    """Read and parse the stage artifacts from a directory of files.

    Fails loudly (``MissingArtifactError``) if any artifact in
    ``STAGE_ARTIFACTS`` is absent. Used by the fixture-based regression gate.

    Special case: the recorded run-195 ``recap.txt`` is the missing-input stub
    (the RECAP grader correctly flags it), so for a healthy regression baseline
    the directory may ship a hand-authored ``recap_good.txt`` that overrides it.
    The raw stub stays on disk for the test suite's documented broken case.
    """
    directory = Path(directory)
    missing = [n for n in STAGE_ARTIFACTS if not (directory / n).exists()]
    if missing:
        raise MissingArtifactError(f"{directory} is missing stage artifact(s): {', '.join(missing)}")
    out: dict = {}
    for n in STAGE_ARTIFACTS:
        path = directory / n
        if n == "recap.txt" and (directory / "recap_good.txt").exists():
            path = directory / "recap_good.txt"
        out[n] = _parse_artifact(n, path.read_text(encoding="utf-8"))
    return out
