"""Best-effort resolution of Google News RSS redirect URLs to the real publisher URL.

We fetch Reuters and Nikkei Asia via Google News search feeds, so their RSS <link> is a
Google-News redirect (``news.google.com/rss/articles/<token>``). Since ~2024 these are NOT plain
HTTP redirects: resolving needs a signature + timestamp scraped from the article page, POSTed to
the undocumented ``batchexecute`` ``Fbv4je`` RPC. Verified 60/61 on real Reuters/Nikkei URLs
(docs/2026-07-02-dedup-poc-findings.md).

STRICTLY BEST-EFFORT: every failure path returns None and the caller keeps the original GN URL.
This depends on undocumented Google internals (the ``data-n-a-sg``/``data-n-a-ts`` attributes and
the RPC shape) and WILL break when Google changes them -- ``tests/test_gnews.py`` ships a
network-marked canary (``-m gnews_live``) so a break surfaces loudly instead of degrading silently.
"""

import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
# Opaque request scaffold for the garturlreq RPC -- copied verbatim from the verified decode; the
# only variable fields are (art_id, ts, sig), appended as the last three elements.
_REQ_PARAMS = [
    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
    "X",
    "X",
    1,
    [1, 1, 1],
    1,
    1,
    None,
    0,
    0,
    None,
    0,
]
_ART_ID_RE = re.compile(r"/articles/([^?/]+)")
_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')


def is_gnews_url(url: str) -> bool:
    return "news.google.com" in (url or "") and "/articles/" in url


def _extract_art_id(url: str) -> str | None:
    m = _ART_ID_RE.search(url or "")
    return m.group(1) if m else None


def _build_payload(art_id: str, ts: str, sig: str) -> bytes:
    garturlreq = ["garturlreq", _REQ_PARAMS, art_id, ts, sig]
    rpc = json.dumps([["Fbv4je", json.dumps(garturlreq)]])
    return ("f.req=" + urllib.parse.quote(rpc)).encode()


def _parse_batchexecute(body: str) -> str | None:
    """Pull the resolved URL out of a batchexecute response. Tries each candidate line
    independently so one malformed line never aborts the parse."""
    for line in body.splitlines():
        if "garturlres" not in line:
            continue
        try:
            inner = json.loads(json.loads(line)[0][2])
            if inner[0] == "garturlres" and isinstance(inner[1], str) and inner[1].startswith("http"):
                return inner[1]
        except Exception:  # malformed candidate line -- skip it, try the next
            continue
    return None


def _http(url: str, *, data: bytes | None = None, timeout: int, delay: float = 0.0) -> str:
    if delay:  # serial pace + jitter; Google exposes no rate-limit headers, so we just go slow
        time.sleep(delay + random.uniform(0, 1))
    headers = {"User-Agent": _UA}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers)  # nosec B310 - fixed https host
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return r.read().decode("utf-8", "replace")


def _fetch(art_id: str, timeout: int, delay: float) -> str | None:
    """The actual two-request decode for one art_id. Returns the publisher URL or None on any
    failure; raises GnewsRateLimited on HTTP 429 so the caller can stop the batch."""
    try:
        html = _http(f"https://news.google.com/articles/{art_id}", timeout=timeout, delay=delay)
        sig, ts = _SIG_RE.search(html), _TS_RE.search(html)
        if not (sig and ts):
            logger.info("gnews: no signature/timestamp in page for %s", art_id[:16])
            return None
        body = _http(
            _BATCHEXECUTE, data=_build_payload(art_id, ts.group(1), sig.group(1)), timeout=timeout, delay=delay
        )
        resolved = _parse_batchexecute(body)
        if not resolved:
            logger.info("gnews: no resolved url in batchexecute response for %s", art_id[:16])
        return resolved
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise GnewsRateLimited() from e
        logger.info("gnews: resolve failed for %s: HTTP %s", art_id[:16], e.code)
        return None
    except Exception as e:  # best-effort; never propagate a resolution failure
        logger.info("gnews: resolve failed for %s: %s: %s", art_id[:16], type(e).__name__, e)
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
    result = _fetch(art_id, timeout, delay)  # may raise GnewsRateLimited (deliberately not cached)
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
