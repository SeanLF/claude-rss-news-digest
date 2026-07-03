"""Tests for digest pipeline pure functions."""

import csv
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

# Add src/ to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import digest as digest_module
from dedup import TfidfMatcher, tokenize
from digest import resolve_article_ids
from feeds import parse_date
from render import (
    _has_article_path,
    estimate_tokens,
    extract_headlines,
    is_safe_url,
    minify_css,
    render_article,
    render_story_feedback_html,
    resolve_css_variables,
    slugify,
    strip_html,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("hello") == 1

    def test_longer_string(self):
        # 40 chars = ~10 tokens
        assert estimate_tokens("a" * 40) == 10


class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<p>Hello</p>") == "Hello"

    def test_decodes_entities(self):
        assert strip_html("&amp; &lt; &gt;") == "& < >"

    def test_normalizes_whitespace(self):
        assert strip_html("Hello   World\n\nTest") == "Hello World Test"

    def test_combined(self):
        assert strip_html("<div>Hello &amp; <b>World</b></div>") == "Hello & World"


class TestIsSafeUrl:
    def test_https_safe(self):
        assert is_safe_url("https://example.com") is True

    def test_http_safe(self):
        assert is_safe_url("http://example.com") is True

    def test_javascript_unsafe(self):
        assert is_safe_url("javascript:alert(1)") is False

    def test_data_unsafe(self):
        assert is_safe_url("data:text/html,<script>") is False

    def test_file_unsafe(self):
        assert is_safe_url("file:///etc/passwd") is False

    def test_empty_unsafe(self):
        assert is_safe_url("") is False


class TestMinifyCss:
    def test_removes_comments(self):
        css = "/* comment */ body { color: red; }"
        assert "comment" not in minify_css(css)

    def test_removes_whitespace(self):
        css = "body {\n  color: red;\n}"
        assert minify_css(css) == "body{color:red;}"

    def test_preserves_functionality(self):
        css = "a { color: blue; } b { font-weight: bold; }"
        result = minify_css(css)
        assert "color:blue" in result
        assert "font-weight:bold" in result


class TestResolveCssVariables:
    def test_resolves_simple_variable(self):
        css = ":root { --bg: white; } body { background: var(--bg); }"
        result = resolve_css_variables(css)
        assert "white" in result
        assert "var(--bg)" not in result

    def test_handles_no_root(self):
        css = "body { color: red; }"
        assert resolve_css_variables(css) == css

    def test_removes_root_block(self):
        css = ":root { --x: 1; } body { color: red; }"
        result = resolve_css_variables(css)
        assert ":root" not in result


class TestParseDate:
    def test_rfc2822_format(self):
        result = parse_date("Tue, 15 Jan 2025 10:30:00 GMT")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_iso_format(self):
        result = parse_date("2025-01-15T10:30:00Z")
        assert result is not None
        assert result.year == 2025

    def test_none_input(self):
        assert parse_date(None) is None

    def test_invalid_format(self):
        assert parse_date("not a date") is None

    def test_empty_string(self):
        assert parse_date("") is None


class TestTokenize:
    def test_lowercases(self):
        assert tokenize("Hello World") == ["hello", "world"]

    def test_removes_punctuation(self):
        assert tokenize("Hello, World!") == ["hello", "world"]

    def test_handles_empty(self):
        assert tokenize("") == []

    def test_removes_stopwords(self):
        assert tokenize("The quick brown fox") == ["quick", "brown", "fox"]
        assert tokenize("I went to the store") == ["went", "store"]


class TestTfidfMatcher:
    def test_exact_match_high_similarity(self):
        matcher = TfidfMatcher(["Train crash kills 21 in India"])
        _, score = matcher.find_most_similar("Train crash kills 21 in India")
        assert score > 0.95

    def test_near_match_high_similarity(self):
        matcher = TfidfMatcher(["Australia shuts dozens of beaches after shark attacks"])
        _, score = matcher.find_most_similar("Australia closes dozens of beaches after shark attacks")
        assert score > 0.8

    def test_same_event_different_numbers(self):
        matcher = TfidfMatcher(["Train crash kills 21 in India"])
        _, score = matcher.find_most_similar("Train crash kills 40 in India")
        assert score > 0.7

    def test_different_topic_low_similarity(self):
        matcher = TfidfMatcher(["Train crash kills 21 in India"])
        _, score = matcher.find_most_similar("Apple announces new iPhone at event")
        assert score < 0.2

    def test_empty_corpus(self):
        matcher = TfidfMatcher([])
        headline, score = matcher.find_most_similar("Any headline")
        assert headline is None
        assert score == 0.0

    def test_empty_query(self):
        matcher = TfidfMatcher(["Some headline"])
        _, score = matcher.find_most_similar("")
        assert score == 0.0

    def test_finds_best_match(self):
        matcher = TfidfMatcher(
            [
                "France passes social media ban for minors",
                "Germany announces new energy policy",
                "Japan earthquake kills dozens",
            ]
        )
        headline, score = matcher.find_most_similar("France approves social media ban for under-15s")
        assert headline == "France passes social media ban for minors"
        assert score > 0.5


class TestRenderStoryFeedbackHtml:
    """One-click HTTP feedback links (replaces the old digest-level mailto)."""

    def test_no_mailto(self):
        result = render_story_feedback_html("digest.example.com", "2026-07-01", "some-story")
        assert "mailto:" not in result

    def test_builds_up_and_down_links(self):
        result = render_story_feedback_html("digest.example.com", "2026-07-01", "some-story")
        assert 'href="https://digest.example.com/feedback?d=2026-07-01&s=some-story&v=up"' in result
        assert 'href="https://digest.example.com/feedback?d=2026-07-01&s=some-story&v=down"' in result

    def test_empty_without_domain(self):
        assert render_story_feedback_html("", "2026-07-01", "some-story") == ""

    def test_empty_without_date(self):
        assert render_story_feedback_html("digest.example.com", "", "some-story") == ""


class TestWriteDigestFeedbackDomainWarning:
    """write_digest should flag a misconfigured DIGEST_DOMAIN loudly, since the
    old digest-level mailto fallback is gone -- silence here means a digest
    ships with no feedback affordance at all."""

    def _stub_write_digest_deps(self, monkeypatch, tmp_path):
        monkeypatch.setattr(digest_module, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(digest_module, "DATA_DIR", tmp_path)
        monkeypatch.setattr(digest_module, "attach_thread_context", lambda selections: selections)
        monkeypatch.setattr(digest_module, "render_digest", lambda *args, **kwargs: "<html></html>")
        monkeypatch.setattr(digest_module, "extract_headlines", lambda selections: [])

    def test_warns_when_digest_domain_missing(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("DIGEST_DOMAIN", raising=False)
        self._stub_write_digest_deps(monkeypatch, tmp_path)

        with caplog.at_level(logging.WARNING, logger="digest"):
            digest_module.write_digest({"must_know": [], "should_know": []}, Path("template.html"))

        assert "DIGEST_DOMAIN" in caplog.text

    def test_no_warning_when_digest_domain_set(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("DIGEST_DOMAIN", "digest.example.com")
        self._stub_write_digest_deps(monkeypatch, tmp_path)

        with caplog.at_level(logging.WARNING, logger="digest"):
            digest_module.write_digest({"must_know": [], "should_know": []}, Path("template.html"))

        assert "DIGEST_DOMAIN" not in caplog.text


class TestResolveArticleIds:
    """Test article_id -> {name, url, bias} resolution."""

    def _write_index(self, tmp_path, index):
        index_path = tmp_path / "article_index.json"
        with open(index_path, "w") as f:
            json.dump(index, f)
        return index_path

    def test_resolves_article_sources(self, tmp_path):
        index = {
            "A1": {
                "url": "https://bbc.com/article",
                "source_id": "bbc",
                "bias": "center",
                "original_title": "BBC headline",
                "name": "BBC World",
            },
        }
        self._write_index(tmp_path, index)

        selections = {
            "must_know": [
                {
                    "headline": "Test story",
                    "summary": "Summary",
                    "why_it_matters": "Why",
                    "sources": [{"article_id": "A1"}],
                }
            ],
            "should_know": [],
            "preheader": "Test",
        }

        with patch("digest.CLAUDE_INPUT_DIR", tmp_path):
            result = resolve_article_ids(selections)

        src = result["must_know"][0]["sources"][0]
        assert src["name"] == "BBC World"
        assert src["url"] == "https://bbc.com/article"
        assert src["bias"] == "center"
        assert src["source_id"] == "bbc"
        assert src["original_title"] == "BBC headline"

    def test_drops_unresolved_ids(self, tmp_path):
        index = {"A1": {"url": "https://x.com", "source_id": "x", "bias": "center", "original_title": "T", "name": "X"}}
        self._write_index(tmp_path, index)

        selections = {
            "must_know": [
                {
                    "headline": "Story",
                    "summary": "S",
                    "why_it_matters": "W",
                    "sources": [{"article_id": "A1"}, {"article_id": "A999"}],
                }
            ],
            "should_know": [],
            "preheader": "Test",
        }

        with patch("digest.CLAUDE_INPUT_DIR", tmp_path):
            result = resolve_article_ids(selections)

        # A1 resolved, A999 dropped
        assert len(result["must_know"][0]["sources"]) == 1
        assert result["must_know"][0]["sources"][0]["name"] == "X"

    def test_drops_story_with_all_unresolved_sources(self, tmp_path):
        index = {}  # Empty -- no articles resolve
        self._write_index(tmp_path, index)

        selections = {
            "must_know": [
                {
                    "headline": "Ghost story",
                    "summary": "S",
                    "why_it_matters": "W",
                    "sources": [{"article_id": "A999"}],
                }
            ],
            "should_know": [],
            "preheader": "Test",
        }

        with patch("digest.CLAUDE_INPUT_DIR", tmp_path):
            result = resolve_article_ids(selections)

        # Story dropped entirely (not left with empty sources)
        assert len(result["must_know"]) == 0

    def test_missing_index_returns_unchanged(self, tmp_path):
        selections = {"must_know": [], "should_know": [], "preheader": "Test"}

        with patch("digest.CLAUDE_INPUT_DIR", tmp_path), patch("digest.OUTPUT_DIR", tmp_path):
            result = resolve_article_ids(selections)

        assert result == selections


class TestPrepareArticleIndex:
    """Test article index generation in prepare_claude_input."""

    def test_article_index_created(self, tmp_path):
        # Set up fetched dir with one source
        fetched_dir = tmp_path / "fetched"
        fetched_dir.mkdir()
        with open(fetched_dir / "bbc.json", "w") as f:
            json.dump(
                [
                    {"title": "Test Article", "url": "https://bbc.com/1", "published": "2026-03-03", "summary": "Sum"},
                ],
                f,
            )

        sources = [
            {
                "id": "bbc",
                "name": "BBC World",
                "url": "https://bbc.com/rss",
                "bias": "center",
                "factuality": "high",
                "perspective": "UK",
            }
        ]

        input_dir = tmp_path / "claude_input"
        output_dir = tmp_path / "output"
        data_dir = tmp_path / "data"

        with (
            patch("prepare.CLAUDE_INPUT_DIR", input_dir),
            patch("prepare.FETCHED_DIR", fetched_dir),
            patch("prepare.OUTPUT_DIR", output_dir),
            patch("prepare.DATA_DIR", data_dir),
            patch("prepare.get_previous_headlines", return_value=[]),
            patch("prepare.log_dedup_action"),
        ):
            from prepare import prepare_claude_input

            prepare_claude_input(sources)

        # Check article_index.json exists
        index_path = input_dir / "article_index.json"
        assert index_path.exists()
        with open(index_path) as f:
            index = json.load(f)

        assert "A1" in index
        assert index["A1"]["url"] == "https://bbc.com/1"
        assert index["A1"]["source_id"] == "bbc"
        assert index["A1"]["bias"] == "center"
        assert index["A1"]["name"] == "BBC World"

        # Check CSV has article_id column, no url
        articles_file = input_dir / "articles_1.csv"
        assert articles_file.exists()
        with open(articles_file) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert "article_id" in rows[0]
        assert "url" not in rows[0]
        assert rows[0]["article_id"] == "A1"

        # Check output dir also has index
        assert (output_dir / "article_index.json").exists()

    @staticmethod
    def _run_prepare(tmp_path, fetched):
        """Write ``fetched`` ({source_id: [article, ...]}) to per-source files, run
        prepare_claude_input over those sources, and return the resulting article_index."""
        fetched_dir = tmp_path / "fetched"
        fetched_dir.mkdir()
        sources = []
        for source_id, articles in fetched.items():
            with open(fetched_dir / f"{source_id}.json", "w") as f:
                json.dump(articles, f)
            sources.append(
                {
                    "id": source_id,
                    "name": source_id.upper(),
                    "url": f"https://{source_id}.example/rss",
                    "bias": "center",
                    "factuality": "high",
                    "perspective": "x",
                }
            )

        with (
            patch("prepare.CLAUDE_INPUT_DIR", tmp_path / "claude_input"),
            patch("prepare.FETCHED_DIR", fetched_dir),
            patch("prepare.OUTPUT_DIR", tmp_path / "output"),
            patch("prepare.DATA_DIR", tmp_path / "data"),
            patch("prepare.get_previous_headlines", return_value=[]),
            patch("prepare.log_dedup_action"),
        ):
            from prepare import prepare_claude_input

            prepare_claude_input(sources)

        with open(tmp_path / "claude_input" / "article_index.json") as f:
            return json.load(f)

    def test_dedups_identical_urls_within_run(self, tmp_path):
        """A feed listing the same URL multiple times (al-monitor emits each article
        under several category tags) must yield ONE article, not one per repeat."""
        index = self._run_prepare(
            tmp_path,
            {
                "al_monitor": [
                    {"title": "Turkish jets strike al-Shabab", "url": "https://al-monitor.com/x/somalia"},
                    {"title": "Turkish jets strike al-Shabab", "url": "https://al-monitor.com/x/somalia"},
                    {"title": "Turkish jets strike al-Shabab", "url": "https://al-monitor.com/x/somalia"},
                    {"title": "Turkish jets strike al-Shabab", "url": "https://al-monitor.com/x/somalia"},
                    {"title": "A different al-Monitor story", "url": "https://al-monitor.com/x/other"},
                ]
            },
        )
        urls = [v["url"] for v in index.values()]
        assert urls.count("https://al-monitor.com/x/somalia") == 1
        assert len(index) == 2

    def test_keeps_distinct_urls_from_same_source(self, tmp_path):
        """Dedup must not over-collapse: distinct URLs stay, even with similar titles."""
        index = self._run_prepare(
            tmp_path,
            {
                "reuters": [
                    {"title": "France wildfires", "url": "https://reuters.com/a"},
                    {"title": "France wildfires update", "url": "https://reuters.com/b"},
                ]
            },
        )
        assert len(index) == 2

    def test_dedups_identical_url_across_sources(self, tmp_path):
        """Two feeds pointing at the exact same page are the same article -- keep one."""
        index = self._run_prepare(
            tmp_path,
            {
                "wire": [{"title": "Shared page", "url": "https://example.com/story"}],
                "reposter": [{"title": "Shared page", "url": "https://example.com/story"}],
            },
        )
        assert len(index) == 1

    def test_collapses_url_variants_that_point_at_one_page(self, tmp_path):
        """Trailing slash, fragment, and scheme/host case are cosmetic -- collapse them."""
        index = self._run_prepare(
            tmp_path,
            {
                "src": [
                    {"title": "Story", "url": "https://example.com/a"},
                    {"title": "Story slash", "url": "https://example.com/a/"},
                    {"title": "Story anchor", "url": "https://example.com/a#comments"},
                    {"title": "Story caps", "url": "HTTPS://Example.com/a"},
                ]
            },
        )
        assert len(index) == 1

    def test_keeps_urls_that_differ_only_by_query(self, tmp_path):
        """The query string carries the article identity (Google News, ?id=) -- never merge on it."""
        index = self._run_prepare(
            tmp_path,
            {
                "src": [
                    {"title": "One", "url": "https://news.example/read?id=1"},
                    {"title": "Two", "url": "https://news.example/read?id=2"},
                ]
            },
        )
        assert len(index) == 2


class TestExtractHeadlinesExpanded:
    """Test per-source headline expansion."""

    def test_one_row_per_source(self):
        selections = {
            "must_know": [
                {
                    "headline": "Big story",
                    "sources": [
                        {"name": "BBC", "source_id": "bbc", "original_title": "BBC title"},
                        {"name": "CNN", "source_id": "cnn", "original_title": "CNN title"},
                    ],
                }
            ],
            "should_know": [],
        }

        headlines = extract_headlines(selections)

        assert len(headlines) == 2
        assert headlines[0]["source_id"] == "bbc"
        assert headlines[0]["original_title"] == "BBC title"
        assert headlines[0]["headline"] == "Big story"
        assert headlines[1]["source_id"] == "cnn"
        assert headlines[1]["original_title"] == "CNN title"

    def test_no_source_id_fn_needed(self):
        """extract_headlines no longer requires a source_id lookup function."""
        selections = {
            "must_know": [{"headline": "Test", "sources": [{"source_id": "x"}]}],
            "should_know": [],
        }
        # Should work without any function parameter
        headlines = extract_headlines(selections)
        assert len(headlines) == 1


class TestHasArticlePath:
    def test_bare_domain(self):
        assert _has_article_path("https://www.washingtonpost.com") is False

    def test_bare_domain_trailing_slash(self):
        assert _has_article_path("https://www.washingtonpost.com/") is False

    def test_article_path(self):
        assert _has_article_path("https://www.washingtonpost.com/politics/article") is True

    def test_short_path(self):
        assert _has_article_path("https://bbc.com/news") is True


class TestRenderArticleSources:
    def _article(self, sources):
        return {
            "headline": "Test",
            "summary": "Summary",
            "why_it_matters": "Why",
            "sources": sources,
        }

    def test_single_source_unchanged(self):
        article = self._article([{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}])
        result = render_article(article, slug="test", include_reporting_varies=False)
        assert '<a href="https://bbc.com/news/1">BBC</a> (center)' in result

    def test_grouped_same_source(self):
        article = self._article(
            [
                {"name": "NYT World", "url": "https://nyt.com/a", "bias": "lean-left"},
                {"name": "NYT World", "url": "https://nyt.com/b", "bias": "lean-left"},
                {"name": "NYT World", "url": "https://nyt.com/c", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test", include_reporting_varies=False)
        # Should NOT repeat source name 3 times
        assert result.count("NYT World") == 1
        # Should have numbered links
        assert "[" in result
        assert '<a href="https://nyt.com/a">1</a>' in result
        assert '<a href="https://nyt.com/b">2</a>' in result
        assert '<a href="https://nyt.com/c">3</a>' in result

    def test_bare_url_fallback_plain_text(self):
        article = self._article([{"name": "WaPo", "url": "https://www.washingtonpost.com", "bias": "lean-left"}])
        result = render_article(article, slug="test", include_reporting_varies=False)
        assert "WaPo (lean-left)" in result
        assert "<a" not in result.split("sources")[1]  # no link in sources line

    def test_mixed_valid_and_bare_urls(self):
        article = self._article(
            [
                {"name": "WaPo", "url": "https://wapo.com/article/1", "bias": "lean-left"},
                {"name": "WaPo", "url": "https://www.washingtonpost.com/", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test", include_reporting_varies=False)
        # One valid URL -- should render as single linked source
        assert '<a href="https://wapo.com/article/1">WaPo</a> (lean-left)' in result

    def test_multiple_different_sources(self):
        article = self._article(
            [
                {"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"},
                {"name": "CNN", "url": "https://cnn.com/story/2", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test", include_reporting_varies=False)
        assert "BBC" in result
        assert "CNN" in result
        assert " · " in result

    def test_no_feedback_links_without_domain(self):
        # digest_domain defaults to "" -- no domain configured means no feedback links,
        # matching the existing no-op behaviour of ARCHIVE_URL/AUTHOR_URL etc.
        article = self._article([{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}])
        result = render_article(article, slug="test", include_reporting_varies=False)
        assert "story-feedback" not in result
        assert "/feedback?" not in result

    def test_feedback_links_present_when_domain_configured(self):
        article = self._article([{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}])
        result = render_article(
            article,
            slug="test-slug",
            include_reporting_varies=False,
            digest_domain="digest.example.com",
            date_url="2026-07-01",
        )
        assert 'href="https://digest.example.com/feedback?d=2026-07-01&s=test-slug&v=up"' in result


class TestRenderArticleWhyItMatters:
    """A coherence-stripped why_it_matters (merge.py blanks the field rather
    than dropping the whole story, see TestFieldAwareCoherenceDegradation in
    test_merge.py) must not leave a dangling "Why it matters:" label with no
    content -- omit the whole <p class="why"> when empty/whitespace."""

    def _article(self, why_it_matters):
        return {
            "headline": "Test",
            "summary": "Summary",
            "why_it_matters": why_it_matters,
            "sources": [{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}],
        }

    def test_empty_why_omits_paragraph(self):
        result = render_article(self._article(""), slug="test", include_reporting_varies=False)
        assert '<p class="why"' not in result
        assert "Why it matters" not in result

    def test_whitespace_only_why_omits_paragraph(self):
        result = render_article(self._article("   "), slug="test", include_reporting_varies=False)
        assert '<p class="why"' not in result
        assert "Why it matters" not in result

    def test_nonempty_why_renders_as_before(self):
        result = render_article(
            self._article("It matters because reasons."), slug="test", include_reporting_varies=False
        )
        assert '<p class="why"><strong>Why it matters:</strong> It matters because reasons.</p>' in result
        assert "mailto:" not in result


class TestSlugify:
    def test_basic(self):
        assert slugify("US Tariffs Spark Trade War") == "us-tariffs-spark-trade-war"

    def test_special_characters(self):
        assert slugify("Oil hits $120 — what's next?") == "oil-hits-120-what-s-next"

    def test_empty(self):
        assert slugify("") == "story"

    def test_all_special(self):
        assert slugify("!@#$%") == "story"

    def test_truncation(self):
        result = slugify("a" * 100)
        assert len(result) == 60

    def test_no_trailing_hyphen_after_truncation(self):
        result = slugify("a" * 59 + " b")
        assert not result.endswith("-")
