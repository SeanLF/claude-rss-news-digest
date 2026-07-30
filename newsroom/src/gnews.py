"""Best-effort resolution of Google News RSS redirect URLs to the real publisher URL.

We fetch Reuters and Nikkei Asia via Google News search feeds, so their RSS <link> is a
Google-News redirect (``news.google.com/rss/articles/<token>``). Since ~2024 these are NOT plain
HTTP redirects: resolving needs a signature + timestamp scraped from the article page, POSTed to
an undocumented ``batchexecute`` RPC.

The decode itself is DELEGATED to ``googlenewsdecoder`` (pinned to our fork; see pyproject). We
hand-rolled that RPC once: it measured 98.4% in a spike on 2026-07-02, shipped with a malformed
request envelope, and resolved **zero** links across the next 25 production digests. The PoC and
the shipped code were never the same code. Delegating puts the envelope somewhere it is
maintained and tested rather than somewhere it is assumed.

What stays ours: the run cache, the paced background prefetch, the deadline, and the 429
back-off, because those encode how *this* pipeline wants to fail (open, quietly, keeping the raw
GN URL) rather than how the decode works.

STRICTLY BEST-EFFORT: every failure path returns None and the caller keeps the original GN URL.
The canary for a future break is no longer a test nobody runs -- ``resolution_stats()`` reports
attempted/succeeded and the caller warns when a run resolves none of many, which is exactly the
signal that went unnoticed for 25 days.
"""

import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class GnewsRateLimited(Exception):
    """Google returned 429 -- an IP-level throttle with no Retry-After. The caller should stop
    resolving for the rest of the run and fall back to raw GN URLs rather than deepen the block."""


# Resolved-URL cache keyed by art_id, shared between the background prefetch thread and the
# render-time resolve() so each token is fetched at most once per run. Stores None for a token
# that was attempted and failed, so we don't retry it.
_cache: dict[str, str | None] = {}
_cache_lock = threading.Lock()
_prefetch_thread: threading.Thread | None = None


_ART_ID_RE = re.compile(r"/articles/([^?/]+)")

# Per-run resolution tally, guarded by _cache_lock because the prefetch thread and the render
# thread both write it. This IS the canary described above: attempted>0 with succeeded==0 is a
# broken decode, and the caller warns on it.
_attempted = 0
_succeeded = 0


def is_gnews_url(url: str) -> bool:
    return "news.google.com" in (url or "") and "/articles/" in url


def _extract_art_id(url: str) -> str | None:
    m = _ART_ID_RE.search(url or "")
    return m.group(1) if m else None


def resolution_stats() -> tuple[int, int]:
    """(attempted, succeeded) decodes this run. Zero succeeded out of many attempted means the
    upstream contract moved again -- alert on it rather than shipping Google links in silence."""
    with _cache_lock:
        return _attempted, _succeeded


def reset_resolution_stats() -> None:
    """Test seam. Production has no caller: a run (including --resume) is one process that makes
    one resolution pass, so the tally starts at zero on its own."""
    global _attempted, _succeeded
    with _cache_lock:
        _attempted = _succeeded = 0


def _fetch(url: str, timeout: int, delay: float) -> str | None:
    """Decode one Google-News URL via googlenewsdecoder. Returns the publisher URL, or None on
    any failure; raises GnewsRateLimited on a 429 so the caller can stop the batch.

    A 429 on ANY attempt backs the run off, including one a later candidate page recovered from,
    which the result dict cannot report. Losing that would let a throttled run keep hammering
    Google and deepen the block.
    """
    global _attempted, _succeeded
    with _cache_lock:
        _attempted += 1
    label = (_extract_art_id(url) or url)[:16]  # log the opaque token, never a reader-facing URL
    try:
        # `decode` rather than driving `decode_flow` ourselves: the library takes `timeout=` now,
        # which was the only reason we ever went below it (GNEWS_RESOLVE_TIMEOUT_S was a dead knob
        # against the hardcoded 15s). It also gets us the library's shared pooled transport rather
        # than one built per call, which was a TLS handshake per decode.
        #
        # We still supply a transport, for one thing the result dict cannot express. A decode tries
        # two candidate article pages, so a 429 on the first that the second recovers from is a
        # refusal we never hear about: the flow reports the LAST failure, and reports nothing at all
        # when it ultimately succeeds. Measured -- 429-then-success and 429-then-503 both arrive
        # with no 429 in the result. The address has begun refusing either way and the run must
        # stand down, so the refusal is observed where every attempt passes.
        #
        # Observed rather than raised: `decode` wraps everything in a fail-open handler, so an
        # exception of ours from inside a transport comes back as a bare failure dict. The
        # `stop_on_429` pattern in the library's docs works at the `drive` layer, below that
        # handler, and this is the same idea one layer up.
        from googlenewsdecoder import TransportError, decode, default_transport

        refused = False
        inner = default_transport()

        def watch(request, **kwargs):
            nonlocal refused
            try:
                return inner(request, **kwargs)
            except TransportError as e:
                if e.status == 429:
                    refused = True
                raise

        result = decode(url, transport=watch, timeout=timeout)
        if delay:
            time.sleep(delay)  # serial pacing, unchanged; `decode` only sleeps for `interval=`

        if refused:
            raise GnewsRateLimited()

        if result.get("status"):
            resolved = result["decoded_url"]
            # Checked at the boundary where a string becomes a link a reader clicks, not because
            # the library is doubted -- `protocol.parse_decoded` ends with this same test. A
            # broken link in a published digest is worth one `startswith` at the trust boundary.
            if resolved.startswith("http"):
                with _cache_lock:
                    _succeeded += 1
                return resolved
            logger.info("gnews: decoder reported success with no usable url for %s", label)
            return None

        logger.info("gnews: resolve failed for %s: %s", label, str(result.get("message"))[:120])
        return None
    except GnewsRateLimited:
        raise  # the caller stops the batch on this; it must outrun the handler below
    except Exception as e:
        # Fail-open, and structural rather than case-by-case: the result handling sits inside the
        # try, so a library that broke its documented shape degrades to a logged None instead of
        # throwing an AttributeError past resolve() and abandoning the remaining links.
        logger.info("gnews: resolve failed for %s: %s: %s", label, type(e).__name__, e)
        return None


def resolve(url: str, *, timeout: int = 15, delay: float = 0.0) -> str | None:
    """Resolve one Google News URL to the publisher URL, or None on any failure (best-effort).
    Returns None for non-GN URLs too. Serves from the run cache when warm (e.g. prefetched),
    otherwise fetches and caches. Raises GnewsRateLimited on HTTP 429."""
    if not is_gnews_url(url):
        return None
    art_id = _extract_art_id(url)
    if not art_id:
        return None
    with _cache_lock:
        if art_id in _cache:
            return _cache[art_id]
    result = _fetch(url, timeout, delay)  # may raise GnewsRateLimited (deliberately not cached)
    with _cache_lock:
        _cache[art_id] = result
    return result


def prefetch_selected(claude_input_dir: Path, *, timeout: int, delay: float, deadline: int) -> None:
    """Fire-and-forget: resolve the SELECTED stories' Google-News links on a background daemon
    thread so resolution overlaps the URL-agnostic stages (WRITE/COHERENCE) instead of blocking
    render. Serial + paced (rate-limit safe), bounded by ``deadline``. Best-effort: any failure
    (missing files, 429, bug) just leaves the cache partial and render falls back to raw GN URLs."""
    global _prefetch_thread
    try:
        selected = json.loads((claude_input_dir / "selected.json").read_text(encoding="utf-8"))
        index = json.loads((claude_input_dir / "article_index.json").read_text(encoding="utf-8"))
        if not isinstance(selected, dict) or not isinstance(index, dict):
            return
        urls: list[str] = []
        seen: set[str] = set()
        for tier in ("must_know", "should_know"):
            for story in selected.get(tier) or []:
                if not isinstance(story, dict):
                    continue
                for aid in story.get("article_ids") or []:
                    entry = index.get(aid)
                    url = entry.get("url") if isinstance(entry, dict) else None
                    if url and is_gnews_url(url) and url not in seen:
                        seen.add(url)
                        urls.append(url)
    except Exception as e:  # broad by design: prefetch must never reach the orchestrator
        logger.info("gnews: prefetch skipped (%s: %s)", type(e).__name__, e)
        return
    if not urls:
        return

    def _work() -> None:
        end = time.monotonic() + deadline
        resolved = 0
        for url in urls:
            if time.monotonic() > end:
                logger.info("gnews: prefetch deadline reached (%d resolved)", resolved)
                return
            try:
                if resolve(url, timeout=timeout, delay=delay):
                    resolved += 1
            except GnewsRateLimited:
                logger.info("gnews: prefetch stopped on 429")
                return

    logger.info("gnews: prefetching %d selected links in the background", len(urls))
    _prefetch_thread = threading.Thread(target=_work, daemon=True, name="gnews-prefetch")
    _prefetch_thread.start()


def wait_for_prefetch(timeout: float) -> bool:
    """Join the background prefetch (if any). Returns True when it's finished (or there was none),
    False if it is still running after ``timeout`` -- in which case render must serve cache-only
    rather than issue synchronous fetches that would race the live thread against Google."""
    t = _prefetch_thread
    if t is None:
        return True
    t.join(timeout)
    return not t.is_alive()


def cached(url: str) -> str | None:
    """The resolved URL for a GN link if already in the run cache, else None. Never fetches --
    used at render when a prefetch is still in flight, so we read without racing it."""
    art_id = _extract_art_id(url)
    if not art_id:
        return None
    with _cache_lock:
        return _cache.get(art_id)
