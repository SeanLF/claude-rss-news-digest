"""Full-text fetch for SELECTED stories (trafilatura).

WRITE and COHERENCE normally see only the ~300-char RSS blurb (`articles_*.csv`). After SELECT
narrows ~460 articles down to the ~19 stories that make the digest, this module fetches the
underlying article pages for those stories' representative articles and extracts readable text,
so WRITE gets richer facts to draw from and COHERENCE has real text to check against.

THE INVARIANT: Claude never sees URLs. This module does all the fetching in Python; the file it
hands to the agents (`article_fulltext.json`) contains article_ids and extracted text ONLY --
never URLs, domains, or source names. (Log lines use the domain only, for the same reason: a full
URL in the logs would leak the thing the pipeline is designed to keep away from the model.)

Strictly best-effort and network-dependent: any failure -- a bad fetch, an unreadable input file,
a bug in this module itself -- must never abort the run. `fetch_for_selected` is wrapped so no
exception class escapes it; on any failure it logs and returns None, and the pipeline proceeds
unaffected (the CSV summaries remain the floor WRITE/COHERENCE always had).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlparse

import config
import trafilatura
from trafilatura.settings import use_config

logger = logging.getLogger(__name__)

# trafilatura's own internal logging (e.g. trafilatura.downloads: `LOGGER.error("download
# error: %s %s", url, err)`) logs the FULL url on fetch failures. By default that record
# propagates up to the root logger's handlers (stdout + the rotating file), which would leak
# the exact thing this module exists to keep away from the model and the logs (see module
# docstring). Cutting propagation at the "trafilatura" logger stops every child logger under it
# (trafilatura.downloads, trafilatura.core, ...) from reaching root, without silencing our own
# `logger` (module-scoped, name "fulltext", unaffected by this).
logging.getLogger("trafilatura").propagate = False

_MAX_WORKERS = 6
_PER_FETCH_TIMEOUT_S = 10

_config_lock_value = None  # lazily-built trafilatura Config, module-cached (see _trafilatura_config)

# A truncated extract may end mid-sentence or mid-number ("...nearly 50"). WRITE has a
# no-completing-cut-off-text rule; without an explicit marker it can't tell a true cut from a
# source that just ends there, and risks "completing" the fact. Matches sentence-ending
# punctuation followed by whitespace or end-of-string.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_TRUNCATION_MARKER = "\n[truncated]"


def _trafilatura_config():
    """A trafilatura Config with an explicit, shorter-than-default download timeout.

    trafilatura's own default (settings.cfg: DOWNLOAD_TIMEOUT=30) is too generous for a
    concurrent batch bounded by an overall step deadline -- one slow/hanging host could eat a
    large share of the deadline on its own. Built once and cached at module scope (a ConfigParser
    build is cheap but there's no reason to redo it per article).
    """
    global _config_lock_value
    if _config_lock_value is None:
        cfg = use_config()
        cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(_PER_FETCH_TIMEOUT_S))
        _config_lock_value = cfg
    return _config_lock_value


def _domain(url: str) -> str:
    """The domain only, for logging -- never the full URL (see module docstring)."""
    try:
        return urlparse(url).netloc or "unknown"
    except ValueError:
        return "unknown"


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_chars``, cutting at the last sentence boundary.

    Text at or under the cap is returned unchanged. Over the cap, cuts at the last
    sentence-ending punctuation within the window and appends a truncation marker so a
    downstream consumer can never mistake the cut for a complete fact. Falls back to a hard cut
    at the cap if no sentence boundary is found in the window (e.g. one very long sentence).
    """
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    matches = list(_SENTENCE_END_RE.finditer(window))
    if matches:
        window = window[: matches[-1].end()]
    return window.rstrip() + _TRUNCATION_MARKER


def _candidate_article_ids(selected: dict, per_story: int) -> list[str]:
    """The article_ids to fetch: the first ``per_story`` ids of every must_know/should_know
    story, deduped across stories (an article can be the representative pick for more than one
    story's cluster in edge cases). SELECT lists representative articles first within a story's
    ``article_ids``, so taking a prefix favours the best-covered sources."""
    seen: set[str] = set()
    ordered: list[str] = []
    for tier in ("must_know", "should_know"):
        for story in selected.get(tier) or []:
            if not isinstance(story, dict):
                continue
            for aid in (story.get("article_ids") or [])[:per_story]:
                if isinstance(aid, str) and aid not in seen:
                    seen.add(aid)
                    ordered.append(aid)
    return ordered


def _fetch_one(article_id: str, url: str, max_chars: int) -> tuple[str, str | None]:
    """Fetch + extract one article. Returns (article_id, text) on success, (article_id, None) on
    any failure -- never raises, so one bad article can't take down the batch."""
    try:
        downloaded = trafilatura.fetch_url(url, config=_trafilatura_config())
    except Exception as e:  # network/parsing errors from trafilatura's stack are not enumerable
        logger.info("fulltext: fetch failed for %s (%s): %s: %s", article_id, _domain(url), type(e).__name__, e)
        return article_id, None
    if not downloaded:
        logger.info("fulltext: fetch returned nothing for %s (%s)", article_id, _domain(url))
        return article_id, None

    try:
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    except Exception as e:
        logger.info("fulltext: extract failed for %s (%s): %s: %s", article_id, _domain(url), type(e).__name__, e)
        return article_id, None
    if not text or not text.strip():
        logger.info("fulltext: extract returned nothing for %s (%s)", article_id, _domain(url))
        return article_id, None

    return article_id, truncate_at_sentence(text.strip(), max_chars)


def _fetch_for_selected_inner(claude_input_dir: Path) -> Path | None:
    out_path = claude_input_dir / "article_fulltext.json"
    # Unlink any stale output up front, before any early return below. Without this, a
    # pre-existing article_fulltext.json from an earlier (successful) run would silently
    # survive every early-return path here (missing inputs, unreadable JSON, all-fetches-fail,
    # ...) and get read by WRITE as if it were fresh -- the freshness guarantee would then rest
    # entirely on prepare.py's rmtree, a non-local invariant this function has no way to see.
    # Mirrors run_stage's unlink-before-attempt. Only reached on the ENABLED path (the disabled
    # short-circuit in fetch_for_selected returns before this function is even called), so a
    # toggle-off-mid-day resume still leaves a stale file in place, which is the accepted
    # behaviour for that case (see fetch_for_selected's docstring).
    out_path.unlink(missing_ok=True)

    selected_path = claude_input_dir / "selected.json"
    index_path = claude_input_dir / "article_index.json"
    if not selected_path.exists() or not index_path.exists():
        logger.warning("fulltext: selected.json or article_index.json missing, skipping")
        return None

    try:
        selected = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("fulltext: failed to read selected.json, skipping: %s: %s", type(e).__name__, e)
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("fulltext: failed to read article_index.json, skipping: %s: %s", type(e).__name__, e)
        return None
    if not isinstance(selected, dict):
        logger.warning("fulltext: selected.json not the expected shape (got %s), skipping", type(selected).__name__)
        return None
    if not isinstance(index, dict):
        logger.warning("fulltext: article_index.json not the expected shape (got %s), skipping", type(index).__name__)
        return None

    candidate_ids = _candidate_article_ids(selected, config.FULLTEXT_PER_STORY)
    tasks: list[tuple[str, str]] = []
    for aid in candidate_ids:
        entry = index.get(aid)
        url = entry.get("url") if isinstance(entry, dict) else None
        if url:
            tasks.append((aid, url))

    if not tasks:
        logger.warning("fulltext: no candidate articles with URLs found, skipping")
        return None

    results: dict[str, str] = {}
    stage_start = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futures = {executor.submit(_fetch_one, aid, url, config.FULLTEXT_MAX_CHARS): aid for aid, url in tasks}
        done, not_done = wait(futures, timeout=config.FULLTEXT_DEADLINE_S)
        for future in done:
            aid, text = future.result()
            if text:
                results[aid] = text
        if not_done:
            logger.warning(
                "fulltext: deadline (%ss) hit, %d/%d fetches still in flight, taking what finished",
                config.FULLTEXT_DEADLINE_S,
                len(not_done),
                len(futures),
            )
    finally:
        # Don't block on in-flight fetches past the deadline: cancel anything not yet started
        # and return immediately. Already-running fetches finish on their own (bounded by the
        # per-fetch timeout) and are simply not waited on.
        executor.shutdown(wait=False, cancel_futures=True)

    elapsed = time.monotonic() - stage_start
    if not results:
        logger.warning("fulltext: 0/%d articles extracted successfully (%.1fs), skipping output", len(tasks), elapsed)
        return None

    payload = {aid: {"text": text} for aid, text in results.items()}
    # Write via a same-dir temp file + atomic rename, not a direct write_text: a crash mid-write
    # (OOM kill, container restart) would otherwise leave a truncated JSON file at out_path that
    # write.md's Read would then load as if it were valid input. Path.replace has os.replace's
    # atomic-on-POSIX-and-Windows semantics; same-dir keeps it on one filesystem (a
    # cross-filesystem rename would not be atomic).
    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)
    logger.info("fulltext: %d/%d articles extracted (%.1fs)", len(results), len(tasks), elapsed)
    return out_path


def fetch_for_selected(claude_input_dir: Path) -> Path | None:
    """Fetch full text for the SELECTED stories' representative articles.

    Reads ``selected.json`` (must_know/should_know stories, each with an ``article_ids`` list)
    and ``article_index.json`` (article_id -> {url, ...}) from ``claude_input_dir``. Fetches the
    first ``config.FULLTEXT_PER_STORY`` article_ids of every story (deduped across stories),
    concurrently, bounded by ``config.FULLTEXT_DEADLINE_S`` overall. Writes
    ``claude_input_dir / "article_fulltext.json"`` as ``{"A12": {"text": "..."}, ...}`` -- only
    articles with a successful extraction, no URLs or other metadata.

    Returns the output path, or None if the step produced no usable output (disabled, missing
    inputs, or every fetch failed) -- callers must treat None as "fall back to the CSV
    summaries", never as an error. This function never raises: any exception is caught, logged,
    and treated the same as "no output".
    """
    if not config.FULLTEXT_ENABLED:
        logger.info("fulltext: disabled (FULLTEXT_ENABLED=false), skipping")
        return None
    try:
        return _fetch_for_selected_inner(claude_input_dir)
    except Exception as e:  # this step must never be able to abort the run
        logger.warning(
            "fulltext: unexpected error, skipping (pipeline unaffected): %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return None
