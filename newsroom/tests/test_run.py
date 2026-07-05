"""Tests for digest pipeline pure functions."""

import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add src/ to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import DEDUP_SIMILARITY_THRESHOLD
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


class TestCrossDayThresholdIsBackstop:
    """The cross-day dedup threshold must be a high-precision near-verbatim backstop, not the old
    0.35 that fired on entity collisions. 2026-07-02 counterfactual (docs/2026-07-02-dedup-poc-findings.md):
    at 0.35, 65% of drops were different stories and ~23% of those were real world-news losses
    (a Guinea-Bissau coup, deadly Kenya protests). SELECT + THREADS own semantic cross-day dedup;
    this filter should only kill obvious near-verbatim repeats."""

    def test_threshold_is_high_precision(self):
        assert DEDUP_SIMILARITY_THRESHOLD >= 0.7  # backstop range, not the FP-heavy 0.35

    def test_entity_collision_is_not_filtered(self):
        # a real 0.35-era false positive: two different stories sharing "divided" must survive
        matcher = TfidfMatcher(["Colombia elects new divided Congress ahead of May presidential vote"])
        _, score = matcher.find_most_similar("Messi Meets Trump and Argentina Is Divided")
        assert score < DEDUP_SIMILARITY_THRESHOLD  # kept, not dropped

    def test_near_verbatim_repeat_is_still_caught(self):
        title = "Firefighters battle blazes in southern France after European heatwave"
        matcher = TfidfMatcher([title])
        _, score = matcher.find_most_similar(title)
        assert score >= DEDUP_SIMILARITY_THRESHOLD  # genuine cross-day repeat still filtered


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

    def _resolve(self, tmp_path, index, article_ids):
        self._write_index(tmp_path, index)
        selections = {
            "must_know": [
                {
                    "headline": "Story",
                    "summary": "S",
                    "why_it_matters": "W",
                    "sources": [{"article_id": a} for a in article_ids],
                }
            ],
            "should_know": [],
            "preheader": "P",
        }
        with patch("digest.CLAUDE_INPUT_DIR", tmp_path):
            return resolve_article_ids(selections)["must_know"][0]["sources"]

    def test_collapses_verbatim_reposts_keeping_wire(self, tmp_path):
        """Reuters + two outlets carrying the identical wire copy -> keep Reuters only.
        (Reuters' RSS title has a ' - Reuters' suffix that must be stripped to match.)"""
        index = {
            "A1": {
                "url": "https://st.com/x",
                "source_id": "straits_times",
                "bias": "center",
                "original_title": "Firefighters battle blazes in southern France",
                "name": "Straits Times",
            },
            "A2": {
                "url": "https://reuters.com/x",
                "source_id": "reuters",
                "bias": "center",
                "original_title": "Firefighters battle blazes in southern France - Reuters",
                "name": "Reuters",
                "wire": True,
            },
            "A3": {
                "url": "https://dm.com/x",
                "source_id": "daily_maverick",
                "bias": "lean-left",
                "original_title": "Firefighters battle blazes in southern France",
                "name": "Daily Maverick",
            },
        }
        sources = self._resolve(tmp_path, index, ["A1", "A2", "A3"])
        assert [s["source_id"] for s in sources] == ["reuters"]

    def test_keeps_distinct_coverage_of_same_story(self, tmp_path):
        """Different headlines = genuinely different articles -> keep all (source diversity)."""
        index = {
            "A1": {
                "url": "https://a.com",
                "source_id": "reuters",
                "bias": "center",
                "original_title": "France wildfires force evacuations - Reuters",
                "name": "Reuters",
            },
            "A2": {
                "url": "https://b.com",
                "source_id": "scmp_world",
                "bias": "center",
                "original_title": "In heatwave-baked France, chaos grips air-conditioner shoppers",
                "name": "SCMP",
            },
        }
        sources = self._resolve(tmp_path, index, ["A1", "A2"])
        assert len(sources) == 2

    def test_compound_hyphen_titles_are_not_truncated_or_merged(self, tmp_path):
        """Regression: only the exact ' - <Source>' suffix is stripped, never a 'US-China'
        style compound. Two distinct headlines that share such a compound must NOT merge."""
        index = {
            "A1": {
                "url": "https://a.com",
                "source_id": "reuters",
                "bias": "center",
                "original_title": "Israel-Gaza ceasefire talks resume in Cairo",
                "name": "Reuters",
            },
            "A2": {
                "url": "https://b.com",
                "source_id": "bbc_world",
                "bias": "center",
                "original_title": "Israel-Gaza ceasefire talks stall amid new disputes",
                "name": "BBC World",
            },
        }
        sources = self._resolve(tmp_path, index, ["A1", "A2"])
        assert len(sources) == 2
        # and the compound survives into the story that IS a repost of a suffixed wire title
        index2 = {
            "B1": {
                "url": "https://c.com",
                "source_id": "reuters",
                "bias": "center",
                "original_title": "Tensions rise in US-China trade dispute - Reuters",
                "name": "Reuters",
                "wire": True,
            },
            "B2": {
                "url": "https://d.com",
                "source_id": "straits_times",
                "bias": "center",
                "original_title": "Tensions rise in US-China trade dispute",
                "name": "Straits Times",
            },
        }
        sources2 = self._resolve(tmp_path, index2, ["B1", "B2"])
        assert [s["source_id"] for s in sources2] == ["reuters"]  # suffix stripped, compound matched

    def test_no_wire_keeps_first_listed(self, tmp_path):
        """No wire present: keep the first-listed source (SELECT's editorial order). We do NOT
        guess which outlet is the reposter -- no hardcoded syndicator list."""
        index = {
            "A1": {
                "url": "https://nyt.com",
                "source_id": "nyt_world",
                "bias": "lean-left",
                "original_title": "After a bitter split, European leaders play nice with Trump",
                "name": "NYT",
            },
            "A2": {
                "url": "https://st.com",
                "source_id": "straits_times",
                "bias": "center",
                "original_title": "After a bitter split, European leaders play nice with Trump",
                "name": "Straits Times",
            },
        }
        sources = self._resolve(tmp_path, index, ["A1", "A2"])
        assert [s["source_id"] for s in sources] == ["nyt_world"]

    def test_wire_wins_regardless_of_position(self, tmp_path):
        """The wire (reuters, perspective==wire_service in sources.json) is canonical even when
        listed last -- attribution follows the origin, not the ordering."""
        index = {
            "A1": {
                "url": "https://st.com",
                "source_id": "straits_times",
                "bias": "center",
                "original_title": "Oil tankers burn near Iraq after strikes",
                "name": "Straits Times",
            },
            "A2": {
                "url": "https://reuters.com",
                "source_id": "reuters",
                "bias": "center",
                "original_title": "Oil tankers burn near Iraq after strikes - Reuters",
                "name": "Reuters",
                "wire": True,
            },
        }
        sources = self._resolve(tmp_path, index, ["A1", "A2"])
        assert [s["source_id"] for s in sources] == ["reuters"]

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
    def _run_prepare(tmp_path, fetched, perspectives=None):
        """Write ``fetched`` ({source_id: [article, ...]}) to per-source files, run
        prepare_claude_input over those sources, and return the resulting article_index.
        ``perspectives`` optionally maps source_id -> perspective (default 'x')."""
        perspectives = perspectives or {}
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
                    "perspective": perspectives.get(source_id, "x"),
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

    def test_captures_wire_flag_from_perspective(self, tmp_path):
        """article_index carries wire=True iff the source's perspective is 'wire_service'
        (the data-driven signal the render layer uses to pick a canonical among reposts)."""
        index = self._run_prepare(
            tmp_path,
            {
                "reuters": [{"title": "Wire story", "url": "https://reuters.example/a"}],
                "bbc": [{"title": "Outlet story", "url": "https://bbc.example/b"}],
            },
            perspectives={"reuters": "wire_service"},
        )
        by_src = {v["source_id"]: v["wire"] for v in index.values()}
        assert by_src == {"reuters": True, "bbc": False}

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

    def test_single_source_row(self):
        article = self._article([{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}])
        result = render_article(article, slug="test")
        assert '<td class="nm">BBC</td>' in result
        assert '<td class="ln">center</td>' in result
        assert '<a href="https://bbc.com/news/1">1</a>' in result

    def test_grouped_same_source(self):
        article = self._article(
            [
                {"name": "NYT World", "url": "https://nyt.com/a", "bias": "lean-left"},
                {"name": "NYT World", "url": "https://nyt.com/b", "bias": "lean-left"},
                {"name": "NYT World", "url": "https://nyt.com/c", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test")
        # One outlet row, three numbered article links.
        assert result.count('<td class="nm">NYT World</td>') == 1
        assert '<a href="https://nyt.com/a">1</a>' in result
        assert '<a href="https://nyt.com/b">2</a>' in result
        assert '<a href="https://nyt.com/c">3</a>' in result

    def test_bare_url_outlet_dropped(self):
        # WaPo's only URL is a bare domain -> the outlet is dropped entirely (a
        # source the reader cannot open is noise); with no usable outlets there
        # is no sources block at all.
        article = self._article([{"name": "WaPo", "url": "https://www.washingtonpost.com", "bias": "lean-left"}])
        result = render_article(article, slug="test")
        assert "WaPo" not in result
        assert "srcbox" not in result

    def test_mixed_valid_and_bare_urls(self):
        article = self._article(
            [
                {"name": "WaPo", "url": "https://wapo.com/article/1", "bias": "lean-left"},
                {"name": "WaPo", "url": "https://www.washingtonpost.com/", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test")
        # One valid URL kept (the bare domain dropped), one WaPo row, one link.
        assert '<td class="nm">WaPo</td>' in result
        assert '<a href="https://wapo.com/article/1">1</a>' in result
        assert "washingtonpost.com/" not in result

    def test_multiple_different_sources(self):
        article = self._article(
            [
                {"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"},
                {"name": "CNN", "url": "https://cnn.com/story/2", "bias": "lean-left"},
            ]
        )
        result = render_article(article, slug="test")
        assert '<td class="nm">BBC</td>' in result
        assert '<td class="nm">CNN</td>' in result
        assert "2 sources" in result

    def test_no_per_story_feedback_rendered(self):
        # Per-story "Useful? Yes/No" links were removed (hostile UX in email, low
        # signal on web) -- guard that they never render again.
        article = self._article([{"name": "BBC", "url": "https://bbc.com/news/1", "bias": "center"}])
        result = render_article(article, slug="test")
        assert "story-feedback" not in result
        assert "/feedback?" not in result
        assert "Useful?" not in result


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

    def test_empty_why_omits_block(self):
        result = render_article(self._article(""), slug="test")
        assert 'class="why"' not in result
        assert "Why it matters" not in result

    def test_whitespace_only_why_omits_block(self):
        result = render_article(self._article("   "), slug="test")
        assert 'class="why"' not in result
        assert "Why it matters" not in result

    def test_nonempty_why_renders_block(self):
        result = render_article(self._article("It matters because reasons."), slug="test")
        assert (
            '<div class="why"><span class="lbl">Why it matters</span><p>It matters because reasons.</p></div>' in result
        )
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
