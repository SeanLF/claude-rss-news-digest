"""Tests for the evolving-thread render treatment (sub-project C) in render.py.

A continuing thread shows the "Ongoing · day N" badge, and -- when there's a delta -- its summary
is replaced with "what's new today" (the top verified facts). Falls back to the WRITE summary on a
quiet day.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import render

BASE = {
    "headline": "European heatwave breaks records",
    "summary": "Temperatures hit 40C across the continent.",
    "why_it_matters": "Infrastructure and health systems are strained.",
    "sources": [{"name": "BBC", "bias": "center", "url": "https://bbc.com/news/heatwave-story"}],
}


def test_render_article_without_thread_is_unchanged():
    out = render.render_article(BASE, slug="heatwave")
    assert "thread-badge" not in out
    assert "European heatwave breaks records" in out


def test_render_article_with_continuing_thread_shows_badge():
    article = {**BASE, "thread": {"day": 4}}
    out = render.render_article(article, slug="heatwave")
    assert 'class="thread-badge">Ongoing · day 4' in out
    # no prose recap -- the badge is the only thread footprint (avoids repeating the summary)
    assert "story so far" not in out.lower()
    assert "still tracking" not in out.lower()


def test_thread_badge_hidden_on_first_day():
    out = render.render_article({**BASE, "thread": {"day": 1}}, slug="x")
    assert "thread-badge" not in out  # day 1 isn't "ongoing" yet


def test_thread_badge_absent_when_thread_empty():
    assert "thread-badge" not in render.render_article({**BASE, "thread": {}}, slug="x")


def test_delta_replaces_summary_for_threaded_story():
    article = {**BASE, "thread": {"day": 4, "delta": "Britain broke its all-time June record today."}}
    out = render.render_article(article, slug="x")
    assert "Britain broke its all-time June record today." in out  # delta is the rendered summary
    assert "Temperatures hit 40C across the continent." not in out  # generic WRITE summary replaced


def test_no_delta_falls_back_to_write_summary():
    out = render.render_article({**BASE, "thread": {"day": 4, "delta": ""}}, slug="x")
    assert "Temperatures hit 40C across the continent." in out  # quiet day -> normal summary
    assert 'class="thread-badge">Ongoing · day 4' in out  # badge still shown


def test_delta_is_html_escaped():
    out = render.render_article({**BASE, "thread": {"day": 2, "delta": "<script>x</script>"}}, slug="x")
    assert "<script>x" not in out
    assert "&lt;script&gt;" in out
