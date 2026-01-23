"""Tests for run.py pure functions."""

import sys
from pathlib import Path

# Add parent to path so we can import run
sys.path.insert(0, str(Path(__file__).parent.parent))

from run import (
    estimate_tokens,
    fix_selections_schema,
    is_safe_url,
    minify_css,
    parse_date,
    resolve_css_variables,
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


class TestFixSelectionsSchema:
    """Tests for Claude output normalization - where bugs hide."""

    def _base_selections(self):
        """Minimal valid selections structure."""
        return {
            "must_know": [],
            "should_know": [],
            "signals": {
                "americas": [],
                "europe": [],
                "asia_pacific": [],
                "middle_east_africa": [],
                "tech": [],
            },
            "regional_summary": {
                "americas": "",
                "europe": "",
                "asia_pacific": "",
                "middle_east_africa": "",
                "tech": "",
            },
        }

    def test_fixes_title_to_headline(self):
        """Claude sometimes uses 'title' instead of 'headline'."""
        selections = self._base_selections()
        selections["must_know"] = [{"title": "Breaking News", "summary": "Sum", "why_it_matters": "Why", "sources": []}]

        result = fix_selections_schema(selections)

        assert result["must_know"][0]["headline"] == "Breaking News"
        assert "title" not in result["must_know"][0]

    def test_fixes_links_array_to_source_urls(self):
        """Claude sometimes puts URLs in a separate links array."""
        selections = self._base_selections()
        selections["must_know"] = [
            {
                "headline": "News",
                "summary": "Sum",
                "why_it_matters": "Why",
                "sources": [{"name": "BBC", "bias": "center"}, {"name": "CNN", "bias": "left"}],
                "links": ["https://bbc.com/1", "https://cnn.com/2"],
            }
        ]

        result = fix_selections_schema(selections)

        assert result["must_know"][0]["sources"][0]["url"] == "https://bbc.com/1"
        assert result["must_know"][0]["sources"][1]["url"] == "https://cnn.com/2"
        assert "links" not in result["must_know"][0]

    def test_adds_missing_why_it_matters(self):
        """Claude sometimes omits why_it_matters."""
        selections = self._base_selections()
        selections["should_know"] = [{"headline": "News", "summary": "Sum", "sources": []}]

        result = fix_selections_schema(selections)

        assert result["should_know"][0]["why_it_matters"] == ""

    def test_fixes_plain_string_signals(self):
        """Claude sometimes outputs signals as plain strings."""
        selections = self._base_selections()
        selections["signals"]["americas"] = ["US economy grows 3%", "Canada election update"]

        result = fix_selections_schema(selections)

        assert result["signals"]["americas"][0]["headline"] == "US economy grows 3%"
        assert "source" in result["signals"]["americas"][0]
        assert result["signals"]["americas"][1]["headline"] == "Canada election update"

    def test_fixes_one_liner_to_headline(self):
        """Claude sometimes uses 'one_liner' instead of 'headline' in signals."""
        selections = self._base_selections()
        selections["signals"]["tech"] = [{"one_liner": "Apple announces new product", "link": "https://apple.com"}]

        result = fix_selections_schema(selections)

        assert result["signals"]["tech"][0]["headline"] == "Apple announces new product"
        assert "one_liner" not in result["signals"]["tech"][0]

    def test_fixes_link_to_source(self):
        """Claude sometimes uses 'link' instead of 'source' object."""
        selections = self._base_selections()
        selections["signals"]["europe"] = [{"headline": "EU news", "link": "https://eu.com"}]

        result = fix_selections_schema(selections)

        assert result["signals"]["europe"][0]["source"]["url"] == "https://eu.com"
        assert "link" not in result["signals"]["europe"][0]

    def test_fixes_string_regional_summary(self):
        """Claude sometimes outputs regional_summary as a single string."""
        selections = self._base_selections()
        selections["regional_summary"] = "Summary of all regions combined."

        result = fix_selections_schema(selections)

        assert isinstance(result["regional_summary"], dict)
        assert result["regional_summary"]["americas"] == "Summary of all regions combined."
        assert result["regional_summary"]["europe"] == ""

    def test_preserves_valid_structure(self):
        """Valid input should pass through unchanged."""
        selections = self._base_selections()
        selections["must_know"] = [
            {
                "headline": "Valid",
                "summary": "Sum",
                "why_it_matters": "Why",
                "sources": [{"name": "BBC", "url": "https://bbc.com", "bias": "center"}],
            }
        ]

        result = fix_selections_schema(selections)

        assert result["must_know"][0]["headline"] == "Valid"
        assert result["must_know"][0]["sources"][0]["url"] == "https://bbc.com"

    def test_handles_empty_selections(self):
        """Empty selections should not crash."""
        result = fix_selections_schema({})
        assert result == {}

    def test_handles_missing_tiers(self):
        """Missing tiers should not crash."""
        result = fix_selections_schema({"must_know": []})
        assert result == {"must_know": []}
