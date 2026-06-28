"""Tests for the evolving-thread render treatment (sub-project C) in render.py."""

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
    assert "thread-continuity" not in out
    assert "European heatwave breaks records" in out


def test_render_article_with_continuing_thread_shows_badge_and_continuity():
    article = {
        **BASE,
        "thread": {
            "day": 4,
            "narrative": "A heat dome has gripped Europe for days; France logged red alerts and dozens of deaths, and Britain has now broken its June record.",
        },
    }
    out = render.render_article(article, slug="heatwave")
    assert 'class="thread-badge">Ongoing · day 4' in out
    assert "The story so far:" in out
    assert "A heat dome has gripped Europe" in out
    # the brittle question ledger is gone
    assert "Still tracking" not in out
    assert "Now answered" not in out


def test_thread_badge_hidden_on_first_day_but_continuity_shows():
    article = {**BASE, "thread": {"day": 1, "narrative": "Day one of the story."}}
    out = render.render_article(article, slug="x")
    assert "thread-badge" not in out  # day 1 isn't "ongoing" yet
    assert "The story so far:" in out


def test_thread_continuity_empty_when_no_narrative():
    assert render._render_thread_continuity({"day": 3, "narrative": None}) == ""
    assert render._render_thread_continuity({"day": 3, "narrative": "  "}) == ""


def test_thread_continuity_escapes_html():
    out = render._render_thread_continuity({"day": 2, "narrative": "<script>x</script>"})
    assert "<script>x" not in out
    assert "&lt;script&gt;" in out
