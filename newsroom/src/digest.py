"""Digest file operations - loading selections, writing digests, file I/O."""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from config import CLAUDE_INPUT_DIR, DATA_DIR, OUTPUT_DIR
from render import extract_headlines, render_digest

logger = logging.getLogger(__name__)


def _source_priority(src: dict) -> int:
    # 0 = wire origin (canonical); 1 = everyone else. The 'wire' flag is data-driven
    # (sources.json perspective == 'wire_service', captured into article_index at prepare time)
    # -- no hardcoded outlet names, no reposter blocklist. Ties resolve to the first-listed
    # source (SELECT's editorial order), since min() is stable; we do NOT guess who reposts wire.
    return 0 if src.get("wire") else 1


def _repost_key(title: str, source_name: str) -> str:
    """Normalize an RSS title for verbatim-repost matching, then lowercase and drop
    punctuation. The only suffix stripped is the exact ' - <Source>' that Google-News-fetched
    feeds (Reuters, Nikkei Asia) append -- matched against the source's OWN name, so we never
    truncate real title content (a 'US-China' compound, a WSJ 'Opinion | ...' clause). Reuters'
    '... - Reuters' thus collapses onto a reposter's bare copy of the same wire story."""
    title = title or ""
    suffix = f" - {source_name}"
    if source_name and title.lower().endswith(suffix.lower()):
        title = title[: -len(suffix)]
    title = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


def collapse_reposts(sources: list[dict]) -> list[dict]:
    """Within one story's resolved source list, collapse verbatim reposts (identical
    normalized title) to a single source-priority canonical, preserving order otherwise.

    Distinct headlines are left untouched -- that is genuine multi-source coverage, not a
    duplicate link. Only near-identical wire copy is collapsed (see A4 in
    docs/2026-07-02-dedup-poc-findings.md)."""
    keyed = [(_repost_key(s.get("original_title", ""), s.get("name", "")), s) for s in sources]

    groups: dict[str, list[dict]] = {}
    for key, src in keyed:
        if key:
            groups.setdefault(key, []).append(src)

    collapsed = []
    emitted: set[str] = set()
    for key, src in keyed:
        if not key:  # untitled: never merge, keep in place
            collapsed.append(src)
            continue
        if key in emitted:
            continue
        emitted.add(key)
        collapsed.append(min(groups[key], key=_source_priority))
    return collapsed


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

    if must_know < 3:
        logger.warning("Only %d must_know stories (expected 3+)", must_know)
    if should_know < 5:
        logger.warning("Only %d should_know stories (expected 5+)", should_know)

    logger.info("Selection complete: %d stories", must_know + should_know)

    return selections


def resolve_article_ids(selections: dict) -> dict:
    """Resolve article_id references to full source metadata.

    Reads article_index.json, replaces {article_id} with {name, url, bias}
    in sources[] (articles). Injects source_id and
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
                "wire": meta.get("wire", False),
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
                item["sources"] = collapse_reposts(resolved_sources)
                resolved_items.append(item)
            else:
                logger.warning("Dropped %s story with no resolved sources: %s", tier, item.get("headline", "?"))
        selections[tier] = resolved_items

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


def attach_thread_context(selections: dict) -> dict:
    """Enrich continuing-thread stories with their badge day-count + delta (today's verified
    what's-new, which replaces the summary) so the renderer can show the living-thread treatment.
    Gated on THREADS_ENABLED; best-effort (the thread layer is additive -- a failure here must not
    break rendering)."""
    import config

    if not config.THREADS_ENABLED:
        return selections
    try:
        import sqlite3

        import db
        import threads

        run_id = db.current_run_id()
        assignments_path = CLAUDE_INPUT_DIR / "thread_assignments.json"
        if run_id is None or not assignments_path.exists():
            return selections
        by_story = {a["story"]: a for a in json.loads(assignments_path.read_text()) if not a.get("is_new")}
        conn = sqlite3.connect(config.DB_PATH)
        try:
            store = threads.ThreadStore(conn)
            for tier in ("must_know", "should_know"):
                for item in selections.get(tier, []):
                    assignment = by_story.get(item.get("cluster_id"))
                    if assignment:
                        item["thread"] = store.render_context(assignment["thread_id"], run_id)
        finally:
            conn.close()
    except Exception:
        logger.warning("Attaching thread context failed (non-fatal)", exc_info=True)
    return selections


def write_digest(selections: dict, template_file: Path) -> Path:
    """Render selections to HTML and write digest file.

    Returns:
        Path to written digest file
    """
    must_know = len(selections.get("must_know", []))
    should_know = len(selections.get("should_know", []))

    logger.info("Rendering: %d must_know, %d should_know", must_know, should_know)

    selections = attach_thread_context(selections)

    # Computed once and reused for both the filename (-> db.digest_date's key) and the
    # per-story feedback links, so a vote's "d" param can never disagree with the date
    # the digest is actually stored/served under.
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%MZ")
    date_url = timestamp[:10]
    digest_domain = os.environ.get("DIGEST_DOMAIN", "")
    if not digest_domain:
        # render_story_feedback_html silently omits the links when digest_domain is
        # empty (there's no mailto fallback anymore) -- a missing/misconfigured
        # DIGEST_DOMAIN would otherwise ship a digest with no feedback affordance.
        logger.warning("DIGEST_DOMAIN is not set -- digest will ship without story feedback links")
    html_content = render_digest(selections, template_file, digest_domain=digest_domain, date_url=date_url)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
