"""Assemble selections.json from subagent outputs.

Replaces the dispatcher's old Step 5 (read draft + coherence, drop fails, call
write_selections MCP tool). The MCP regurgitation was fragile -- a stream idle
timeout while the parent was generating ~50 KB of JSON could nuke the run.
Doing it in Python after the dispatcher exits removes the failure mode.
"""

import json
import logging
from pathlib import Path

from schema import validate_selections

logger = logging.getLogger(__name__)


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

    draft = json.loads(draft_path.read_text())
    coherence = json.loads(coherence_path.read_text())
    cluster_map = _load_cluster_map(claude_input_dir)

    failed = {r["headline"] for r in coherence.get("results", []) if not r.get("pass", True)}

    dropped = []
    for tier in ("must_know", "should_know"):
        kept = []
        for item in draft.get(tier, []):
            if item.get("headline") in failed:
                dropped.append((tier, item.get("headline")))
            else:
                _attach_cluster_id(item, item.get("sources", []), cluster_map)
                kept.append(item)
        draft[tier] = kept

    if dropped:
        logger.info("Coherence dropped %d headlines:", len(dropped))
        for section, headline in dropped:
            logger.info("  [%s] %s", section, headline)

    if not draft.get("must_know"):
        raise RuntimeError(
            f"Assembled selections has no must_know entries (dropped {len(dropped)} via coherence); aborting to avoid empty broadcast"
        )

    errors = validate_selections(draft)
    if errors:
        raise RuntimeError("Assembled selections failed schema validation:\n  - " + "\n  - ".join(errors[:10]))

    output_path = claude_input_dir / "selections.json"
    output_path.write_text(json.dumps(draft, indent=2))

    must_know = len(draft["must_know"])
    should_know = len(draft["should_know"])
    logger.info("Assembled selections.json: %d must_know, %d should_know", must_know, should_know)

    return output_path
