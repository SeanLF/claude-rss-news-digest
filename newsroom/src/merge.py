"""Assemble selections.json from subagent outputs.

Replaces the dispatcher's old Step 5 (read draft + coherence, drop fails, call
write_selections MCP tool). The MCP regurgitation was fragile -- a stream idle
timeout while the parent was generating ~50 KB of JSON could nuke the run.
Doing it in Python after the dispatcher exits removes the failure mode.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

from eval_graders import grade_selections
from schema import validate_selections

logger = logging.getLogger(__name__)

# Footer garnish length cap -- matches SELECTIONS_SCHEMA's not_covered_blurb maxLength
# (see eval_graders.GraderLimits.preheader_max_chars for the same pattern).
_NOT_COVERED_BLURB_MAX_LEN = 300

# The not_covered_blurb is reader-facing (rendered in the digest footer), but it
# originates from SELECT, whose working vocabulary includes internal cluster
# indices ("cluster 132", "clusters 0, 1") and opaque article IDs ("[A221]").
# Those must never reach a reader (cf. the 2026-06-30 [A221] delta_from_facts
# leak). If the blurb still carries them, we DROP it (degrade to no footer)
# rather than sanitise freeform prose -- and warn, so a prompt regression is
# visible in logs instead of shipping garbage.
_INTERNAL_ID_PATTERNS = (
    re.compile(r"\bclusters?\s+\d", re.IGNORECASE),  # "cluster 132", "clusters 0, 1"
    re.compile(r"\[A\d+\]"),  # bracketed article IDs like "[A221]"
)


def _norm_headline(headline: str) -> str:
    """Normalize a headline for resilient fallback matching.

    COHERENCE re-types each headline into its report, so the string can drift
    from WRITE's original (smart quotes, dashes, trailing punctuation, casing).
    Normalizing both sides keeps the fallback match from silently missing.
    """
    text = unicodedata.normalize("NFKC", headline or "")
    for fancy, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", "-"), ("–", "-")):
        text = text.replace(fancy, plain)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".,;:!?").casefold()
    return text


def _item_article_ids(item: dict) -> frozenset[str]:
    """The set of cited article_ids for a draft item (its stable identity)."""
    return frozenset(
        s["article_id"] for s in item.get("sources", []) if isinstance(s, dict) and isinstance(s.get("article_id"), str)
    )


def _result_matches(result: dict, item_ids: frozenset[str], item_norm_headline: str) -> bool:
    """Whether a coherence result refers to a given draft item.

    Prefers the opaque article_ids (drift-proof); falls back to the normalized
    headline when the result carries no article_ids (legacy-shaped reports).

    If two draft items ever cite an identical article_ids set (unusual -- SELECT
    separates stories), a pass:false result will match and drop both. That is an
    accepted conservative failure mode: over-dropping is safer than silently
    keeping an unverified headline, which is the bug this matching replaced.
    """
    rids = result.get("article_ids")
    if rids and item_ids and frozenset(rids) == item_ids:
        return True
    rh = result.get("headline")
    return rh is not None and _norm_headline(rh) == item_norm_headline


def _coherence_failed(result: dict) -> bool:
    """Whether a coherence result should be treated as FAILED (drop trigger).

    Strict pass semantics: only the literal boolean ``True`` counts as a pass.
    The report is model-generated JSON, so a non-boolean "pass" value (e.g. the
    JSON string ``"false"`` -- TRUTHY if used raw -- or ``"true"``) or an
    omitted "pass" key on an otherwise-present entry is a plausible drift.
    Both are treated as a FAILURE, matching this module's "over-dropping is
    safer than silently keeping an unverified headline" stance (see
    ``_result_matches``). A warning names the story and the offending value so
    prompt drift toward non-boolean output is visible in run logs.
    """
    value = result.get("pass")
    if value is True:
        return False
    if value is False:
        return True
    if "pass" in result:
        logger.warning(
            "Coherence result for %r has non-boolean pass=%r; treating as FAILED",
            result.get("headline"),
            value,
        )
    else:
        logger.warning(
            "Coherence result for %r is missing 'pass'; treating as FAILED",
            result.get("headline"),
        )
    return True


def _why_it_matters_only_failure(result: dict) -> bool:
    """Whether a FAILED coherence result's failed_fields is usable AND names
    exactly why_it_matters -- the one case merge.py degrades gracefully instead
    of dropping the whole story.

    WRITE habitually seasons why_it_matters with true-but-uncited background
    specifics ("6-3", "$60bn", "last major city in Darfur"); COHERENCE catches
    these correctly, but a headline-drop-on-any-fail policy was costing up to
    35% of stories on a real archived day even though only one field was ever
    wrong. Any other shape -- failed_fields absent, empty, unparseable (not a
    list), or naming headline/summary/an unknown field alongside or instead of
    why_it_matters -- returns False, so the caller falls back to a full drop.
    Over-dropping is safer than silently keeping an unverified headline or
    summary; only why_it_matters gets the softer treatment.
    """
    fields = result.get("failed_fields")
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        return False
    return set(fields) == {"why_it_matters"}


def _grade_assembled(selections: dict) -> None:
    """Run the L1 code-assertion graders as a NON-FATAL production assertion.

    The graders are an observability floor, not a gate: a failed check (length
    over cap, count out of range, etc.) is logged as a warning so it shows up in
    run logs, but it NEVER raises -- the run must not abort on a soft-quality
    signal after schema validation has already passed. Any unexpected grader
    error is itself swallowed (warned), since this is best-effort instrumentation.
    """
    try:
        report = grade_selections(selections)
    except Exception as e:  # broad by design: instrumentation must never break the run
        logger.warning("L1 graders errored (non-fatal, skipping): %s", e)
        return
    failures = report.failures
    if not failures:
        logger.info("L1 graders: all %d checks passed", len(report.checks))
        return
    logger.warning("L1 graders flagged %d check(s) (non-fatal):", len(failures))
    for check in failures:
        logger.warning("  [%s] %s", check.name, check.detail)


def _load_cluster_map(claude_input_dir: Path) -> dict[str, str]:
    """Build an article_id -> cluster story label map from clusters.json.

    Best-effort: a missing or malformed clusters.json yields an empty map and a
    warning rather than failing the run. The story label is the cluster
    identifier we persist for overlap/redundancy analysis.
    """
    clusters_path = claude_input_dir / "clusters.json"
    if not clusters_path.exists():
        logger.warning("clusters.json missing -- shown headlines will have no cluster_id")
        return {}
    try:
        clusters = json.loads(clusters_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("clusters.json unreadable (%s) -- skipping cluster_id mapping", e)
        return {}

    mapping: dict[str, str] = {}
    for cluster in clusters.get("clusters", []):
        story = cluster.get("story")
        if not story:
            continue
        for article_id in cluster.get("article_ids", []):
            mapping[article_id] = story
    return mapping


def _attach_cluster_id(item: dict, sources: list[dict], cluster_map: dict[str, str]) -> None:
    """Attach the cluster story label to a draft item from its source article_ids.

    Uses the first source whose article_id maps to a cluster. No-op when the map
    is empty or none of the article_ids are known.
    """
    if not cluster_map:
        return
    for src in sources:
        aid = src.get("article_id")
        story = cluster_map.get(aid) if isinstance(aid, str) else None
        if story:
            item["cluster_id"] = story
            return


def _load_not_covered_blurb(claude_input_dir: Path) -> str | None:
    """Read the optional not_covered_blurb footer garnish from selected.json.

    Best-effort by design: this is WRITE-stage context (see select.md) that we
    also surface to readers, never load-bearing. Any failure mode here --
    missing file, invalid JSON, missing/empty/wrong-typed field -- yields None
    (field absent from the assembled output) rather than raising. A value over
    the schema cap is truncated rather than dropped, so a verbose SELECT
    output still degrades to *something* instead of silently vanishing.
    """
    selected_path = claude_input_dir / "selected.json"
    if not selected_path.exists():
        # Missing file is the routine case (SELECT chose not to emit the
        # garnish, or an older selected.json predates this field) -- info,
        # not warning. Deliberately lower severity than _load_cluster_map's
        # missing-file warning: cluster_id is a tracked field, this is garnish.
        logger.info("selected.json missing -- no not_covered_blurb to surface")
        return None
    try:
        # ValueError covers json.JSONDecodeError (a subclass) and
        # UnicodeDecodeError (also a ValueError subclass, raised by
        # read_text() on non-UTF8 bytes) -- both are real read/parse
        # failures, not the benign absent case, so this branch warns.
        selected = json.loads(selected_path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("selected.json unreadable (%s) -- skipping not_covered_blurb", e)
        return None
    blurb = selected.get("not_covered_blurb") if isinstance(selected, dict) else None
    if blurb is not None and not isinstance(blurb, str):
        logger.warning(
            "not_covered_blurb has wrong type %s -- omitting from footer: %s",
            type(blurb).__name__,
            repr(blurb)[:80],
        )
        return None
    if not blurb or not blurb.strip():
        logger.debug("not_covered_blurb absent or empty in selected.json")
        return None
    blurb = blurb.strip()
    leaked = next((p.pattern for p in _INTERNAL_ID_PATTERNS if p.search(blurb)), None)
    if leaked is not None:
        logger.warning(
            "not_covered_blurb leaks internal ids (matched /%s/) -- dropping from footer rather than "
            "exposing them to readers: %s",
            leaked,
            repr(blurb)[:120],
        )
        return None
    if len(blurb) > _NOT_COVERED_BLURB_MAX_LEN:
        logger.warning(
            "not_covered_blurb exceeds %d chars (%d) -- truncating rather than dropping",
            _NOT_COVERED_BLURB_MAX_LEN,
            len(blurb),
        )
        blurb = _truncate_on_word_boundary(blurb, _NOT_COVERED_BLURB_MAX_LEN)
    return blurb


def _truncate_on_word_boundary(text: str, max_len: int) -> str:
    """Truncate to <= max_len chars ending in an ellipsis, cutting on a word
    boundary so we never emit a mid-word fragment (cf. the "...SO…" that shipped
    2026-07-02). Reserves one char for the ellipsis; if the first word alone
    exceeds the budget, falls back to a hard cut. Text already within max_len is
    returned unchanged -- self-guarding so callers need not pre-check length."""
    if len(text) <= max_len:
        return text
    budget = max_len - 1  # reserve room for the ellipsis
    head = text[:budget]
    if not text[budget].isspace() and not text[budget - 1].isspace():
        # We cut mid-word -- back up to the last whitespace so the final word is whole.
        cut = head.rfind(" ")
        if cut > 0:
            head = head[:cut]
    return head.rstrip() + "…"


def assemble_selections(claude_input_dir: Path) -> Path:
    """Read draft + coherence, drop coherence-failed entries, write selections.json.

    Also maps each surviving story back to its CLUSTER story label (via
    clusters.json) and attaches it as ``cluster_id`` for redundancy tracking.

    Raises:
        RuntimeError: if intermediate files missing/invalid or assembled output
            fails schema validation.
    """
    draft_path = claude_input_dir / "draft_selections.json"
    coherence_path = claude_input_dir / "coherence_report.json"

    if not draft_path.exists():
        raise RuntimeError(f"draft_selections.json missing: {draft_path}")
    if not coherence_path.exists():
        raise RuntimeError(f"coherence_report.json missing: {coherence_path}")

    try:
        draft = json.loads(draft_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"draft_selections.json unreadable: {e}") from e
    try:
        coherence = json.loads(coherence_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"coherence_report.json unreadable: {e}") from e
    cluster_map = _load_cluster_map(claude_input_dir)

    results = coherence.get("results", [])
    failed = [r for r in results if _coherence_failed(r)]
    matched_failed: set[int] = set()

    dropped = []
    for tier in ("must_know", "should_know"):
        kept = []
        for item in draft.get(tier, []):
            item_ids = _item_article_ids(item)
            item_norm = _norm_headline(item.get("headline", ""))

            # Coverage: surface any headline COHERENCE never reported on -- it was
            # supposed to check EVERY headline, so a gap is a silent miss.
            if not any(_result_matches(r, item_ids, item_norm) for r in results):
                logger.warning("Coherence report has no entry for headline: %s", item.get("headline"))

            hits = [i for i, r in enumerate(failed) if _result_matches(r, item_ids, item_norm)]
            if hits:
                matched_failed.update(hits)
                # Graceful degradation: if EVERY matching failure is a usable
                # why_it_matters-only failure, keep the story and blank just that
                # field instead of dropping it. Any other case (mixed fields,
                # unparseable, unknown names) is a full drop, same as before.
                # NOTE: the L1 no_empty_fields grader also flags the blanked
                # field (non-fatal) -- expected double signal on this path.
                if all(_why_it_matters_only_failure(failed[i]) for i in hits):
                    item["why_it_matters"] = ""
                    reasons = "; ".join(str(failed[i].get("reason") or "(no reason given by COHERENCE)") for i in hits)
                    logger.warning("coherence stripped why_it_matters: %s: %s", item.get("headline"), reasons)
                    _attach_cluster_id(item, item.get("sources", []), cluster_map)
                    kept.append(item)
                else:
                    dropped.append((tier, item.get("headline")))
            else:
                _attach_cluster_id(item, item.get("sources", []), cluster_map)
                kept.append(item)
        draft[tier] = kept

    if dropped:
        logger.info("Coherence dropped %d headlines:", len(dropped))
        for section, headline in dropped:
            logger.info("  [%s] %s", section, headline)

    # A pass:false entry that matched no draft headline means a coherence failure
    # was silently NOT applied (headline drift, stale report) -- never swallow it.
    for i, r in enumerate(failed):
        if i not in matched_failed:
            logger.warning("Coherence flagged a headline not found in draft (no drop applied): %s", r.get("headline"))

    if not draft.get("must_know"):
        raise RuntimeError(
            f"Assembled selections has no must_know entries (dropped {len(dropped)} via coherence); aborting to avoid empty broadcast"
        )

    not_covered_blurb = _load_not_covered_blurb(claude_input_dir)
    if not_covered_blurb:
        draft["not_covered_blurb"] = not_covered_blurb

    errors = validate_selections(draft)
    if errors:
        raise RuntimeError("Assembled selections failed schema validation:\n  - " + "\n  - ".join(errors[:10]))

    _grade_assembled(draft)

    output_path = claude_input_dir / "selections.json"
    output_path.write_text(json.dumps(draft, indent=2))

    must_know = len(draft["must_know"])
    should_know = len(draft["should_know"])
    logger.info("Assembled selections.json: %d must_know, %d should_know", must_know, should_know)

    return output_path
