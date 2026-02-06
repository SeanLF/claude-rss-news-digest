"""Digest file operations - loading selections, writing digests, file I/O."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from config import DATA_DIR, OUTPUT_DIR, SOURCES_FILE
from feeds import get_source_id_by_name
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

    # Log counts (MCP already validated structure)
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


def read_shown_headlines() -> list[dict]:
    """Read shown_headlines.json output from Claude."""
    headlines_file = DATA_DIR / "shown_headlines.json"
    if not headlines_file.exists():
        return []

    try:
        with open(headlines_file) as f:
            return json.load(f)
    except OSError, json.JSONDecodeError:
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

    # Extract and write headlines for deduplication
    def get_source_id(name: str) -> str | None:
        return get_source_id_by_name(name, SOURCES_FILE)

    headlines = extract_headlines(selections, get_source_id)
    headlines_file = DATA_DIR / "shown_headlines.json"
    with open(headlines_file, "w") as f:
        json.dump(headlines, f, indent=2)

    logger.info("Wrote %s (%d stories)", digest_path.name, len(headlines))

    return digest_path


def find_latest_digest() -> Path | None:
    """Find most recent digest file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    digests = sorted(OUTPUT_DIR.glob("digest-*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return digests[0] if digests else None
