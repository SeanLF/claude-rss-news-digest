"""Tests for digest pipeline pure functions."""

import sys
from pathlib import Path

# Add src/ to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dedup import TfidfMatcher, tokenize
from feeds import parse_date
from render import (
    estimate_tokens,
    generate_feedback_html,
    is_safe_url,
    minify_css,
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


class TestGenerateFeedbackHtml:
    def test_contains_all_buttons(self):
        result = generate_feedback_html("test@example.com")
        assert "Love it" in result
        assert "Good" in result
        assert "So so" in result

    def test_mailto_links(self):
        result = generate_feedback_html("test@example.com")
        assert 'href="mailto:test@example.com?subject=Feedback: Love it"' in result
        assert 'href="mailto:test@example.com?subject=Feedback: Good"' in result
        assert 'href="mailto:test@example.com?subject=Feedback: So so"' in result

    def test_escapes_html_in_email(self):
        result = generate_feedback_html("test+tag@example.com")
        assert "test+tag@example.com" in result

    def test_escapes_special_chars(self):
        result = generate_feedback_html("<script>@evil.com")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
