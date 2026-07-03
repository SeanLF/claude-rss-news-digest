"""Google News URL resolver.

Offline tests exercise the decode LOGIC (id extraction, payload shape, response parsing, and
best-effort failure) against fixtures -- no network, so they run in CI. The live canary at the
bottom actually hits Google and is skipped unless GNEWS_LIVE=1; run it out-of-band (or on a
schedule) to catch Google changing the batchexecute contract. See gnews.py header.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gnews


@pytest.fixture(autouse=True)
def _clear_gnews_state():
    """The resolved-URL cache and prefetch handle are module-global -- reset around each test."""
    gnews._cache.clear()
    gnews._prefetch_thread = None
    yield
    gnews._cache.clear()
    gnews._prefetch_thread = None


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


class TestBuildPayload:
    def test_embeds_variable_fields(self):
        payload = gnews._build_payload("ART123", "1700000000", "SIGVALUE").decode()
        # url-encoded f.req carrying the three per-article fields
        assert payload.startswith("f.req=")
        assert "ART123" in payload and "1700000000" in payload and "SIGVALUE" in payload
        assert "Fbv4je" in payload and "garturlreq" in payload


class TestParseBatchexecute:
    def _response(self, url):
        # faithful shape: a line whose [0][2] is a JSON string ["garturlres", <url>]
        line = json.dumps([["wrb.fr", "Fbv4je", json.dumps(["garturlres", url]), None, None, None, "generic"]])
        return ")]}'\n\n" + line

    def test_pulls_resolved_url(self):
        body = self._response("https://www.reuters.com/world/europe/foo")
        assert gnews._parse_batchexecute(body) == "https://www.reuters.com/world/europe/foo"

    def test_none_when_no_garturlres(self):
        assert gnews._parse_batchexecute(')]}\'\n[["wrb.fr","Other","[]"]]') is None

    def test_skips_malformed_lines_and_finds_later_match(self):
        good = json.dumps([["wrb.fr", "Fbv4je", json.dumps(["garturlres", "https://x.com/a"]), None]])
        body = "garturlres this line is not json\n" + good
        assert gnews._parse_batchexecute(body) == "https://x.com/a"


class TestResolveBestEffort:
    def test_non_gnews_url_returns_none_without_network(self, monkeypatch):
        # guard: must not touch the network for a non-GN url
        monkeypatch.setattr(gnews, "_http", lambda *a, **k: pytest.fail("no network expected"))
        assert gnews.resolve("https://www.reuters.com/world/foo") is None

    def test_network_failure_is_swallowed(self, monkeypatch):
        def boom(*a, **k):
            raise urllib.request.URLError("429")

        monkeypatch.setattr(gnews, "_http", boom)
        assert gnews.resolve("https://news.google.com/rss/articles/CBMiABC?oc=5") is None

    def test_missing_signature_returns_none(self, monkeypatch):
        monkeypatch.setattr(gnews, "_http", lambda *a, **k: "<html>no attrs here</html>")
        assert gnews.resolve("https://news.google.com/rss/articles/CBMiABC?oc=5") is None

    def test_429_raises_rate_limited_so_caller_can_stop(self, monkeypatch):
        def http429(*a, **k):
            raise urllib.error.HTTPError("https://news.google.com", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(gnews, "_http", http429)
        with pytest.raises(gnews.GnewsRateLimited):
            gnews.resolve("https://news.google.com/rss/articles/CBMiABC?oc=5")

    def test_non_429_http_error_is_swallowed(self, monkeypatch):
        def http500(*a, **k):
            raise urllib.error.HTTPError("https://news.google.com", 500, "err", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(gnews, "_http", http500)
        assert gnews.resolve("https://news.google.com/rss/articles/CBMiABC?oc=5") is None

    def test_resolve_caches_per_art_id(self, monkeypatch):
        fetched = []
        monkeypatch.setattr(gnews, "_fetch", lambda art, t, d: fetched.append(art) or "https://pub/x")
        u = "https://news.google.com/rss/articles/CBMiSAME?oc=5"
        assert gnews.resolve(u) == "https://pub/x"
        assert gnews.resolve(u) == "https://pub/x"  # second call served from cache
        assert len(fetched) == 1

    def test_happy_path_resolves(self, monkeypatch):
        page = '<c-wiz data-n-a-id="x" data-n-a-sg="SIG" data-n-a-ts="1700000000">...</c-wiz>'
        be = ")]}'\n" + json.dumps(
            [["wrb.fr", "Fbv4je", json.dumps(["garturlres", "https://www.reuters.com/world/real"]), None]]
        )
        calls = iter([page, be])
        monkeypatch.setattr(gnews, "_http", lambda *a, **k: next(calls))
        assert (
            gnews.resolve("https://news.google.com/rss/articles/CBMiABC?oc=5") == "https://www.reuters.com/world/real"
        )


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
        sel = self._selections("https://news.google.com/rss/articles/CBMiABC?oc=5")
        digest._resolve_gnews_links(sel)
        assert sel["must_know"][0]["sources"][0]["url"] == "https://www.reuters.com/real"

    def test_two_sources_same_article_fetch_once(self, monkeypatch):
        import digest

        fetched = []
        monkeypatch.setattr(gnews, "_fetch", lambda art, t, d: fetched.append(art) or "https://pub/x")
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
        sel = self._selections("https://news.google.com/rss/articles/CBMiABC?oc=5")
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
                "url": "https://news.google.com/rss/articles/CBMiABC?oc=5",
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
        monkeypatch.setattr(gnews, "_fetch", lambda art, t, d: fetched.append(art) or "https://pub/real")
        gnews.prefetch_selected(tmp_path, timeout=1, delay=0, deadline=5)
        gnews.wait_for_prefetch(5)
        assert fetched == [gnews._extract_art_id(gn)]
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
    Fails loudly if Google has changed the batchexecute contract. Not run in CI."""
    import re

    feed = gnews._http("https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en", timeout=20)
    link = re.search(r"<link>(https://news\.google\.com/rss/articles/[^<]+)</link>", feed)
    assert link, "could not find a GN article link in the Reuters feed"
    resolved = gnews.resolve(link.group(1), timeout=20)
    assert resolved and resolved.startswith("http") and "news.google.com" not in resolved, (
        f"GN decode returned {resolved!r} -- Google may have changed the RPC; update gnews.py"
    )
