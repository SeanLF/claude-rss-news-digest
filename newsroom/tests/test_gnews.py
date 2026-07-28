"""Google News URL resolver.

The decode itself now belongs to ``googlenewsdecoder``; these tests cover the part that is still
ours -- id extraction, the adapter's failure handling, the 429 back-off, the run cache and the
prefetch -- by substituting the library in ``sys.modules``. No network, so they run in CI.

The real break detector is no longer a test. ``gnews.resolution_stats()`` is checked on every
production run and warns on attempted>0 / succeeded==0, because the previous canary was gated
behind GNEWS_LIVE=1, nothing ever set it, and a decoder that broke on 2026-07-03 shipped Google
interstitial links for 25 digests. The live canary at the bottom is kept as a manual probe.
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gnews

# One arbitrary GN article link, reused wherever the specific token does not matter. The cache is
# keyed by token, and the autouse fixture clears it, so tests never leak into each other.
_GN_URL = "https://news.google.com/rss/articles/CBMiABC?oc=5"


@pytest.fixture(autouse=True)
def _clear_gnews_state():
    """Cache, prefetch handle and resolution tally are module-global -- reset around each test."""
    gnews._cache.clear()
    gnews._prefetch_thread = None
    gnews.reset_resolution_stats()
    yield
    gnews._cache.clear()
    gnews._prefetch_thread = None
    gnews.reset_resolution_stats()


class TestIsGnewsUrl:
    def test_true_for_gnews_article(self):
        assert gnews.is_gnews_url("https://news.google.com/rss/articles/CBMiABC?oc=5") is True

    def test_false_for_publisher_url(self):
        assert gnews.is_gnews_url("https://www.reuters.com/world/foo") is False

    def test_false_for_gnews_non_article(self):
        assert gnews.is_gnews_url("https://news.google.com/rss/search?q=site:reuters.com") is False

    def test_false_for_empty(self):
        assert gnews.is_gnews_url("") is False


class TestExtractArtId:
    def test_extracts_token(self):
        assert gnews._extract_art_id("https://news.google.com/rss/articles/CBMiXYZ?oc=5") == "CBMiXYZ"

    def test_none_when_absent(self):
        assert gnews._extract_art_id("https://news.google.com/rss/search?q=x") is None


@pytest.fixture
def decoder(monkeypatch):
    """Install a stand-in for googlenewsdecoder. _fetch imports the module at call time, so
    substituting it in sys.modules is the seam. Pass the result dict the decode should produce,
    or a callable that stands in for the whole decode.

    This stubs the sans-I/O surface `_fetch` actually uses -- `drive`, `decode_flow`,
    `RequestsTransport`, `TransportError` -- rather than the `gnewsdecoder` shortcut, because
    `_fetch` drives the flow itself in order to pass a timeout the shortcut does not accept.

    A stub cannot notice the real library changing shape under it: that is what
    `test_live_canary_resolves_a_fresh_gnews_url` is for.
    """

    def install(result, *, transport_error=None):
        module = types.ModuleType("googlenewsdecoder")

        class TransportError(Exception):
            def __init__(self, message="", status=None):
                super().__init__(message)
                self.status = status

        module.TransportError = TransportError
        module.RequestsTransport = lambda *a, **k: lambda request, **kw: ""
        module.decode_flow = lambda url, *a, **k: url  # opaque handle; drive() below defines it

        def drive(flow, transport, **kwargs):
            # `flow` is the URL, per decode_flow above. Exercise the transport wrapper the way
            # the real drive() does, so _raise_on_429 is under test rather than bypassed.
            if transport_error is not None:
                transport(_Req(), **kwargs)
            return result(flow) if callable(result) else result

        module.drive = drive

        class _Req:
            method, url, headers, body = "GET", "https://news.google.com/articles/T", {}, None

        if transport_error is not None:
            code = transport_error

            def failing(*a, **k):
                raise TransportError(f"HTTP {code}", status=code)

            module.RequestsTransport = lambda *a, **k: failing

        module.gnewsdecoder = result if callable(result) else (lambda *a, **k: result)
        monkeypatch.setitem(sys.modules, "googlenewsdecoder", module)

    return install


class TestResolveBestEffort:
    def test_non_gnews_url_returns_none_without_network(self, decoder):
        # guard: must not touch the decoder for a non-GN url
        decoder(lambda *a, **k: pytest.fail("no decode"))
        assert gnews.resolve("https://www.reuters.com/world/foo") is None

    def test_decoder_exception_is_swallowed(self, decoder):
        def boom(*a, **k):
            raise RuntimeError("connection reset")

        decoder(boom)
        assert gnews.resolve(_GN_URL) is None

    def test_decoder_failure_status_returns_none(self, decoder):
        decoder({"status": False, "message": "Footer missing"})
        assert gnews.resolve(_GN_URL) is None

    def test_success_without_a_usable_url_returns_none(self, decoder):
        # defensive: a truthy status with a junk payload must not become a reader-facing link
        decoder({"status": True, "decoded_url": "notaurl"})
        assert gnews.resolve(_GN_URL) is None

    def test_non_dict_result_returns_none(self, decoder):
        # a library that stops honouring its result contract must degrade, not raise past resolve()
        decoder("https://pub/x")
        assert gnews.resolve(_GN_URL) is None

    @pytest.mark.parametrize(
        "message",
        ["HTTP Error 429: Too Many Requests", "429 Client Error", "Too Many Requests for url"],
        ids=["http-429", "client-429", "prose"],
    )
    def test_429_raises_rate_limited_so_caller_can_stop(self, decoder, message):
        # The library reports no typed error, so the back-off can only key off the message. If
        # this stops matching, a throttled run hammers Google instead of standing down.
        decoder({"status": False, "message": message})
        with pytest.raises(gnews.GnewsRateLimited):
            gnews.resolve(_GN_URL)

    def test_other_failures_do_not_raise_rate_limited(self, decoder):
        decoder({"status": False, "message": "500 whoops"})
        assert gnews.resolve(_GN_URL) is None

    def test_resolve_caches_per_art_id(self, monkeypatch):
        fetched = []
        monkeypatch.setattr(gnews, "_fetch", lambda url, t, d: fetched.append(url) or "https://pub/x")
        assert gnews.resolve(_GN_URL) == "https://pub/x"
        assert gnews.resolve(_GN_URL) == "https://pub/x"  # second call served from cache
        assert len(fetched) == 1

    def test_happy_path_resolves(self, decoder):
        decoder({"status": True, "decoded_url": "https://www.reuters.com/world/real"})
        assert gnews.resolve(_GN_URL) == "https://www.reuters.com/world/real"

    def test_the_url_reaches_the_decoder_not_the_bare_art_id(self, decoder):
        # The library parses the token out of the URL itself; handing it a bare id decodes nothing.
        seen = []
        decoder(lambda url, **k: seen.append(url) or {"status": True, "decoded_url": "https://pub/x"})
        gnews.resolve(_GN_URL)
        assert seen == [_GN_URL]


class TestResolutionStatsCanary:
    """attempted>0 with succeeded==0 is the signal that went unnoticed for 25 digests."""

    def test_counts_success(self, decoder):
        decoder({"status": True, "decoded_url": "https://pub/x"})
        gnews.resolve(_GN_URL)
        assert gnews.resolution_stats() == (1, 1)

    def test_total_failure_is_visible_as_attempted_without_success(self, decoder):
        decoder({"status": False, "message": "400 Bad Request"})
        for token in ("CBMiONE", "CBMiTWO", "CBMiTHREE"):
            gnews.resolve(f"https://news.google.com/rss/articles/{token}?oc=5")
        assert gnews.resolution_stats() == (3, 0)

    def test_cache_hits_are_not_counted_as_fresh_attempts(self, decoder):
        decoder({"status": True, "decoded_url": "https://pub/x"})
        gnews.resolve(_GN_URL)
        gnews.resolve(_GN_URL)
        assert gnews.resolution_stats() == (1, 1)


class TestResolveGnewsLinksWiring:
    """The digest.py glue that upgrades shown GN links -- must be gated, cached, and never crash."""

    def _selections(self, *urls):
        return {
            "must_know": [{"headline": "S", "sources": [{"url": u, "source_id": "reuters"} for u in urls]}],
            "should_know": [],
        }

    def test_upgrades_gnews_urls_in_place(self, monkeypatch):
        import digest

        monkeypatch.setattr("gnews.resolve", lambda url, timeout=15, delay=0: "https://www.reuters.com/real")
        sel = self._selections(_GN_URL)
        digest._resolve_gnews_links(sel)
        assert sel["must_know"][0]["sources"][0]["url"] == "https://www.reuters.com/real"

    def test_two_sources_same_article_fetch_once(self, monkeypatch):
        import digest

        fetched = []
        monkeypatch.setattr(gnews, "_fetch", lambda url, t, d: fetched.append(url) or "https://pub/x")
        # two shown sources point at the same GN article -> the module cache fetches it once
        u = "https://news.google.com/rss/articles/CBMiSAME?oc=5"
        sel = self._selections(u, u)
        digest._resolve_gnews_links(sel)
        assert len(fetched) == 1
        assert all(s["url"] == "https://pub/x" for s in sel["must_know"][0]["sources"])

    def test_stops_batch_on_rate_limit_keeping_raw_urls(self, monkeypatch):
        import digest

        def rate_limited(*a, **k):
            raise gnews.GnewsRateLimited()

        monkeypatch.setattr("gnews.resolve", rate_limited)
        sel = self._selections(
            "https://news.google.com/rss/articles/CBMiA?oc=5",
            "https://news.google.com/rss/articles/CBMiB?oc=5",
        )
        digest._resolve_gnews_links(sel)  # must not raise
        assert all(s["url"].startswith("https://news.google.com") for s in sel["must_know"][0]["sources"])

    def test_stops_at_deadline_keeping_raw_urls(self, monkeypatch):
        import digest

        # first monotonic() sets the deadline, second (the check) is far past it -> stop before any resolve
        times = iter([0.0, 1e9])
        monkeypatch.setattr(digest.time, "monotonic", lambda: next(times))
        monkeypatch.setattr("gnews.resolve", lambda *a, **k: pytest.fail("must not resolve past the deadline"))
        sel = self._selections("https://news.google.com/rss/articles/CBMiA?oc=5")
        digest._resolve_gnews_links(sel)
        assert sel["must_know"][0]["sources"][0]["url"].startswith("https://news.google.com")

    def test_uses_cache_only_while_prefetch_in_flight(self, monkeypatch):
        """If the background prefetch hasn't finished, render must read cache-only, never issue a
        synchronous fetch that would race the live thread against Google."""
        import digest

        class _StillRunning:
            def join(self, timeout):
                pass

            def is_alive(self):
                return True

        monkeypatch.setattr(gnews, "_prefetch_thread", _StillRunning())
        monkeypatch.setattr("gnews.resolve", lambda *a, **k: pytest.fail("must not fetch while prefetch runs"))
        warm = "https://news.google.com/rss/articles/CBMiWARM?oc=5"
        cold = "https://news.google.com/rss/articles/CBMiCOLD?oc=5"
        gnews._cache[gnews._extract_art_id(warm)] = "https://pub/warm"
        sel = self._selections(warm, cold)
        digest._resolve_gnews_links(sel)
        urls = [s["url"] for s in sel["must_know"][0]["sources"]]
        assert urls[0] == "https://pub/warm"  # warmed link upgraded from cache
        assert urls[1].startswith("https://news.google.com")  # cold link left raw (no racing fetch)

    def test_noop_when_disabled(self, monkeypatch):
        import config
        import digest

        monkeypatch.setattr(config, "GNEWS_RESOLVE_ENABLED", False)
        monkeypatch.setattr("gnews.resolve", lambda *a, **k: pytest.fail("must not resolve when disabled"))
        sel = self._selections(_GN_URL)
        digest._resolve_gnews_links(sel)  # no crash, no resolve
        assert sel["must_know"][0]["sources"][0]["url"].startswith("https://news.google.com")

    def test_resolver_error_never_breaks_resolve_article_ids(self, monkeypatch, tmp_path):
        """The whole step is wrapped so even a throwing resolver leaves rendering intact."""
        import json as _json

        import digest

        def boom(*a, **k):
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr("gnews.resolve", boom)
        index = {
            "A1": {
                "url": _GN_URL,
                "source_id": "reuters",
                "bias": "center",
                "original_title": "T",
                "name": "Reuters",
                "wire": True,
            }
        }
        (tmp_path / "article_index.json").write_text(_json.dumps(index))
        sel = {
            "must_know": [{"headline": "S", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}],
            "should_know": [],
            "preheader": "p",
        }
        with patch("digest.CLAUDE_INPUT_DIR", tmp_path):
            out = digest.resolve_article_ids(sel)  # must not raise
        assert out["must_know"][0]["sources"][0]["url"].startswith("https://news.google.com")


class TestPrefetch:
    """Background prefetch after SELECT warms the cache so render is a cache hit (off critical path)."""

    def _write(self, tmp_path, selected, index):
        (tmp_path / "selected.json").write_text(json.dumps(selected))
        (tmp_path / "article_index.json").write_text(json.dumps(index))

    def test_prefetch_warms_cache_for_selected_links(self, tmp_path, monkeypatch):
        gn = "https://news.google.com/rss/articles/CBMiPRE?oc=5"
        self._write(tmp_path, {"must_know": [{"article_ids": ["A1"]}], "should_know": []}, {"A1": {"url": gn}})
        fetched = []
        monkeypatch.setattr(gnews, "_fetch", lambda url, t, d: fetched.append(url) or "https://pub/real")
        gnews.prefetch_selected(tmp_path, timeout=1, delay=0, deadline=5)
        gnews.wait_for_prefetch(5)
        assert fetched == [gn]  # the decoder needs the URL; the cache is still keyed by art_id
        assert gnews.resolve(gn) == "https://pub/real"  # render is now a pure cache hit
        assert len(fetched) == 1

    def test_prefetch_missing_files_is_noop(self, tmp_path):
        gnews.prefetch_selected(tmp_path, timeout=1, delay=0, deadline=5)  # no selected.json -> no crash
        gnews.wait_for_prefetch(1)
        assert gnews._prefetch_thread is None

    def test_prefetch_malformed_shape_is_noop(self, tmp_path, monkeypatch):
        # well-formed JSON, wrong shape (tier not a list, story not a dict) must not crash
        self._write(tmp_path, {"must_know": "nope", "should_know": [42]}, {"A1": {"url": "x"}})
        monkeypatch.setattr(gnews, "_fetch", lambda *a: pytest.fail("should not fetch"))
        gnews.prefetch_selected(tmp_path, timeout=1, delay=0, deadline=5)  # no crash
        gnews.wait_for_prefetch(1)
        assert gnews._prefetch_thread is None


@pytest.mark.skipif(not __import__("os").environ.get("GNEWS_LIVE"), reason="live network canary; run with GNEWS_LIVE=1")
def test_live_canary_resolves_a_fresh_gnews_url():
    """Hits Google for real: pull the first article from the Reuters GN feed and resolve it.
    A manual probe for reproducing a break by hand -- production's real detector is the
    attempted/succeeded warning in digest._resolve_gnews_links, which cannot be skipped."""
    import re
    import urllib.request

    req = urllib.request.Request(  # nosec B310 - fixed https host
        "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310
        feed = r.read().decode("utf-8", "replace")
    link = re.search(r"<link>(https://news\.google\.com/rss/articles/[^<]+)</link>", feed)
    assert link, "could not find a GN article link in the Reuters feed"
    resolved = gnews.resolve(link.group(1), timeout=20)
    assert resolved and resolved.startswith("http") and "news.google.com" not in resolved, (
        f"GN decode returned {resolved!r} -- upstream contract may have moved; bump googlenewsdecoder"
    )
