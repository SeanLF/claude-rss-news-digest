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


def assemble_selections(claude_input_dir: Path) -> Path:
    """Read draft + coherence, drop coherence-failed entries, write selections.json.

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

    failed = {r["headline"] for r in coherence.get("results", []) if not r.get("pass", True)}

    dropped = []
    for tier in ("must_know", "should_know"):
        kept = []
        for item in draft.get(tier, []):
            if item.get("headline") in failed:
                dropped.append((tier, item.get("headline")))
            else:
                kept.append(item)
        draft[tier] = kept

    for region, items in draft.get("signals", {}).items():
        kept = []
        for item in items:
            if item.get("headline") in failed:
                dropped.append((f"signals.{region}", item.get("headline")))
            else:
                kept.append(item)
        draft["signals"][region] = kept

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
    signals = sum(len(v) for v in draft["signals"].values())
    logger.info("Assembled selections.json: %d must_know, %d should_know, %d signals", must_know, should_know, signals)

    return output_path
