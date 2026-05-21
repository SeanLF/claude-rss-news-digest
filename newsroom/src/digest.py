"""Digest file operations - loading selections, writing digests, file I/O."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from config import CLAUDE_INPUT_DIR, DATA_DIR, OUTPUT_DIR
from render import REGION_ORDER, extract_headlines, render_digest

logger = logging.getLogger(__name__)


def load_selections(selections_file: Path) -> dict:
    """Load selections.json and log story counts.

    Raises:
        RuntimeError: If file is missing or invalid JSON
    """
    if not selections_file.exists():
        raise RuntimeError("selections.json not found - Claude failed to create output")

    try:
        with open(selections_file) as f:
            selections = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"selections.json is invalid JSON: {e}") from e

    # Log counts (structure already validated in merge.assemble_selections)
    must_know = len(selections.get("must_know", []))
    should_know = len(selections.get("should_know", []))
    signals = selections.get("signals", {})
    signals_count = sum(len(signals.get(c, [])) for c in REGION_ORDER)

    if must_know < 3:
        logger.warning("Only %d must_know stories (expected 3+)", must_know)
    if should_know < 5:
        logger.warning("Only %d should_know stories (expected 5+)", should_know)

    logger.info("Selection complete: %d stories", must_know + should_know + signals_count)

    return selections


def resolve_article_ids(selections: dict) -> dict:
    """Resolve article_id references to full source metadata.

    Reads article_index.json, replaces {article_id} with {name, url, bias}
    in sources[] (articles) and source{} (signals). Injects source_id and
    original_title for downstream recording.

    Falls back gracefully when article_index.json is missing (--write-only mode).
    Drops entries with unresolved article_ids.
    """
    # Try CLAUDE_INPUT_DIR first, fall back to OUTPUT_DIR (for --write-only)
    index_path = CLAUDE_INPUT_DIR / "article_index.json"
    if not index_path.exists():
        index_path = OUTPUT_DIR / "article_index.json"
    if not index_path.exists():
        logger.warning("article_index.json not found -- article_ids will not be resolved")
        return selections

    try:
        with open(index_path) as f:
            article_index = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("article_index.json is corrupt: %s", e)
        return selections

    unresolved_count = 0

    def resolve_source(src: dict) -> dict | None:
        """Resolve a single {article_id} to {name, url, bias, source_id, original_title}."""
        nonlocal unresolved_count
        article_id = src.get("article_id")
        if not article_id:
            return src  # Already resolved or no article_id

        meta = article_index.get(article_id)
        if not meta:
            unresolved_count += 1
            logger.warning("Unresolved article_id: %s", article_id)
            return None

        try:
            return {
                "name": meta["name"],
                "url": meta["url"],
                "bias": meta["bias"],
                "source_id": meta["source_id"],
                "original_title": meta["original_title"],
            }
        except KeyError as e:
            logger.warning("Incomplete metadata for %s (missing %s)", article_id, e)
            unresolved_count += 1
            return None

    # Resolve articles (must_know, should_know) -- sources is a list
    # Drop stories where ALL sources are unresolved (no attribution to render)
    for tier in ["must_know", "should_know"]:
        resolved_items = []
        for item in selections.get(tier, []):
            resolved_sources = []
            for src in item.get("sources", []):
                resolved = resolve_source(src)
                if resolved:
                    resolved_sources.append(resolved)
            if resolved_sources:
                item["sources"] = resolved_sources
                resolved_items.append(item)
            else:
                logger.warning("Dropped %s story with no resolved sources: %s", tier, item.get("headline", "?"))
        selections[tier] = resolved_items

    # Resolve signals -- source is a single object
    signals = selections.get("signals", {})
    for region in REGION_ORDER:
        resolved_items = []
        for item in signals.get(region, []):
            src = item.get("source", {})
            resolved = resolve_source(src)
            if resolved:
                item["source"] = resolved
                resolved_items.append(item)
        signals[region] = resolved_items

    if unresolved_count:
        logger.warning("Dropped %d unresolved article_id references", unresolved_count)

    return selections


def read_shown_headlines() -> list[dict]:
    """Read shown_headlines.json output from Claude."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if not headlines_file.exists():
        return []

    try:
        with open(headlines_file) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read shown_headlines.json: %s", e)
        return []


def cleanup_shown_headlines():
    """Remove shown_headlines.json after successful run."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if headlines_file.exists():
        headlines_file.unlink()


def write_digest(selections: dict, template_file: Path) -> Path:
    """Render selections to HTML and write digest file.

    Returns:
        Path to written digest file
    """
    must_know = len(selections.get("must_know", []))
    should_know = len(selections.get("should_know", []))
    signals = selections.get("signals", {})
    signals_count = sum(len(signals.get(c, [])) for c in REGION_ORDER)

    logger.info("Rendering: %d must_know, %d should_know, %d signals", must_know, should_know, signals_count)

    html_content = render_digest(selections, template_file)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%MZ")
    digest_path = OUTPUT_DIR / f"digest-{timestamp}.html"
    digest_path.write_text(html_content)

    # Extract headlines for deduplication (one row per source per story)
    headlines = extract_headlines(selections)
    headlines_file = DATA_DIR / "shown_headlines.json"
    with open(headlines_file, "w") as f:
        json.dump(headlines, f, indent=2)

    logger.info("Wrote %s (%d headline rows)", digest_path.name, len(headlines))

    return digest_path


def find_latest_digest() -> Path | None:
    """Find most recent digest file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None
