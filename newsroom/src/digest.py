"""Digest file operations - loading selections, writing digests, file I/O."""

import json
import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from config import CLAUDE_INPUT_DIR, DATA_DIR, OUTPUT_DIR
from render import extract_headlines, render_digest

logger = logging.getLogger(__name__)

# Below this many decode attempts, "zero succeeded" is not evidence of anything. One article
# Google cannot decode is an ordinary occurrence; a whole contract moving is not.
_CANARY_MIN_ATTEMPTS = 3


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


def _collapse_key(src: dict) -> str:
    """What makes two entries in ONE story's source list the same item.

    Title only. Keying on the shared wire agency was tried and REVERTED -- it was wrong in
    both directions, measured against a real run:

    - It over-collapsed. Four DISTINCT Reuters articles on the Korea chip deals all keyed
      to `agency:reuters` and folded into one link. An outlet is never a repost of itself,
      and WRITE had cited all four.
    - It under-collapsed. Reuters' own item and a VERBATIM Straits Times repost of it
      stopped collapsing, because Reuters' entry got an agency key while Straits Times --
      which publishes no byline and no dateline -- kept a title key, so they diverged.

    The case it was built for (a rewritten headline over shared wire copy) needs BOTH
    entries to carry detected provenance, and detection covers ~7% of the corpus
    concentrated in three feeds. So it rarely fired and sometimes did harm.

    `wire_agency` is still resolved and still rendered -- crediting "AFP · via SCMP" is
    independent of, and more valuable than, merging the rows. Merging rewritten headlines
    needs a similarity measure (title unigram containment measured 57% recall at precision
    1.00), not an equality key. See docs/2026-07-25-feed-sourcing-findings.md.
    """
    return _repost_key(src.get("original_title", ""), src.get("name", ""))


def collapse_reposts(sources: list[dict]) -> list[dict]:
    """Within one story's resolved source list, collapse reposts of the same item to a
    single source-priority canonical, preserving order otherwise.

    Two things count as the same item (see `_collapse_key`): a shared wire agency, or --
    where provenance is unknown -- a verbatim identical normalized title. Distinct
    headlines from outlets with no shared agency are left untouched; that is genuine
    multi-source coverage, not a duplicate link (see A4 in
    docs/2026-07-02-dedup-poc-findings.md)."""
    keyed = [(_collapse_key(s), s) for s in sources]

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
                # .get, never [...]: --write-only re-renders from a PERSISTED article_index
                # that may predate this field, and a KeyError here is caught below and drops
                # the source -- lose them all and the whole story is dropped. Absent simply
                # degrades collapse_reposts to its title-only behaviour.
                "wire_agency": meta.get("wire_agency"),
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

    try:
        _resolve_gnews_links(selections)
    except Exception as e:  # best-effort: a broken resolver must never break rendering
        logger.warning("gnews: link resolution skipped after error: %s", e)
    return selections


def _resolve_gnews_links(selections: dict) -> None:
    """Best-effort: upgrade Google-News redirect links on the SHOWN sources to the real
    publisher URL (cleaner reader links for Reuters/Nikkei). Only the ~handful of GN links that
    survived collapse are resolved; results are cached per art_id within the run. Any failure
    leaves the GN URL in place -- this must never break rendering. Gated by GNEWS_RESOLVE_ENABLED."""
    import config
    import gnews

    if not config.GNEWS_RESOLVE_ENABLED:
        return
    gn_sources = [
        src
        for tier in ("must_know", "should_know")
        for item in selections.get(tier, [])
        for src in item.get("sources", [])
        if gnews.is_gnews_url(src.get("url", ""))
    ]
    # Prefer the background prefetch started after SELECT. If it has finished, resolve() is a cache
    # hit for prefetched links and a synchronous fetch for anything not warmed (e.g. --write-only,
    # which never ran orchestrate). If it is STILL running, read cache-only so we never issue a
    # fetch that races the live thread against Google.
    prefetch_done = gnews.wait_for_prefetch(config.GNEWS_RESOLVE_DEADLINE_S)
    upgraded = 0
    rate_limited = False
    deadline = time.monotonic() + config.GNEWS_RESOLVE_DEADLINE_S
    for src in gn_sources:
        if prefetch_done:
            if time.monotonic() > deadline:
                logger.warning("gnews: resolve deadline reached, keeping raw GN URLs for the rest")
                break
            try:
                resolved = gnews.resolve(
                    src["url"], timeout=config.GNEWS_RESOLVE_TIMEOUT_S, delay=config.GNEWS_RESOLVE_DELAY_S
                )
            except gnews.GnewsRateLimited:
                logger.warning("gnews: rate-limited (429), stopping link resolution for this run")
                rate_limited = True
                break
        else:
            resolved = gnews.cached(src["url"])  # prefetch still in flight -- don't race it
        if resolved:
            src["url"] = resolved
            upgraded += 1

    # THE CANARY -- do not collapse this back into `if upgraded:`. That shape logged nothing when
    # a run resolved nothing, which is how a decoder broken from 2026-07-03 shipped Google
    # interstitial links for 25 consecutive digests unnoticed.
    #
    # Three conditions on it, each removing a way of crying wolf. An alert that fires on routine
    # conditions trains the reader to ignore it, which costs more than the alert buys:
    #
    #   not rate_limited   a 429 also produces zero successes, and it is a NORMAL outcome that
    #                      production has already hit. It has its own warning above, which says
    #                      what actually happened instead of blaming the library.
    #   attempted >= MIN   only Reuters and Nikkei arrive via GN feeds, and collapse_reposts
    #                      thins that further, so single-digit attempts are the usual case. One
    #                      undecodable article is not evidence the contract moved.
    #   not upgraded       the reader-facing quantity. `succeeded` counts the PREFETCH
    #                      population (every article_id of every selected story); `upgraded`
    #                      counts the links actually shown. Keying off `succeeded` let a run
    #                      where every shown link failed stay quiet because unshown ones worked.
    attempted, succeeded = gnews.resolution_stats()
    if gn_sources and not upgraded and not rate_limited and attempted >= _CANARY_MIN_ATTEMPTS:
        logger.warning(
            "gnews: upgraded 0 of %d shown links (%d decode attempts, %d succeeded) -- the decoder "
            "contract has probably moved again; readers are getting news.google.com interstitials. "
            "Check `googlenewsdecoder` for an update.",
            len(gn_sources),
            attempted,
            succeeded,
        )
    elif gn_sources:
        logger.info(
            "gnews: upgraded %d of %d Google-News links to publisher URLs (%d decode attempts)",
            upgraded,
            len(gn_sources),
            attempted,
        )


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


def _shared_cluster_ids(selections: dict) -> set[str]:
    """cluster_ids carried by 2+ selected items across both tiers.

    ``attach_thread_context`` looks up the thread delta by ``cluster_id`` and the delta REPLACES the
    summary, so two selections sharing a cluster_id would render an identical card (the run-235 bug).
    A collision reaches here two ways: a residual duplicate cluster *label*, or the coarse
    ``merge._attach_cluster_id`` heuristic (first source that maps to any cluster) assigning one
    cluster_id to two genuinely distinct stories. Either way, thread enrichment must be skipped.
    """
    counts = Counter(
        item.get("cluster_id")
        for tier in ("must_know", "should_know")
        for item in selections.get(tier, [])
        if item.get("cluster_id")
    )
    return {cid for cid, n in counts.items() if n > 1}


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
        # A shared cluster_id would give both stories the SAME delta (which replaces the summary),
        # rendering identical cards. Skip enrichment for those (they keep their distinct WRITE
        # summaries) rather than drop them -- degrade the garnish, never the story.
        shared = _shared_cluster_ids(selections)
        conn = sqlite3.connect(config.DB_PATH)
        try:
            store = threads.ThreadStore(conn)
            for tier in ("must_know", "should_know"):
                for item in selections.get(tier, []):
                    cluster_id = item.get("cluster_id")
                    if cluster_id in shared:
                        # error, not warning: reaching here means an upstream invariant broke (a
                        # residual duplicate cluster label or a coarse _attach_cluster_id mapping) --
                        # a real defect to chase, surfaced above routine WARNING noise. Non-fatal.
                        logger.error(
                            "Skipping thread enrichment for %r: cluster_id %r shared by multiple "
                            "selections (thread delta would render them identically)",
                            item.get("headline"),
                            cluster_id,
                        )
                        continue
                    assignment = by_story.get(cluster_id)
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

    # Drives the digest filename (-> db.digest_date's key).
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%MZ")
    html_content = render_digest(selections, template_file)

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
