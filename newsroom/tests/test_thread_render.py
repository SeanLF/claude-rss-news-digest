"""Tests for the evolving-thread render treatment (sub-project C) in render.py.

A continuing thread shows the "Ongoing · day N" eyebrow, and -- when there's a delta -- its
lede/summary is replaced with "what's new today" (the top verified facts). Falls back to the
WRITE summary on a quiet day.
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


def test_render_article_without_thread_has_no_eyebrow():
    out = render.render_article(BASE, slug="heatwave")
    assert "eyebrow" not in out
    assert "European heatwave breaks records" in out


def test_render_article_with_continuing_thread_shows_eyebrow():
    article = {**BASE, "thread": {"day": 4}}
    out = render.render_article(article, slug="heatwave")
    assert '<p class="eyebrow"><span class="loc">Ongoing</span> · day 4</p>' in out
    # no prose recap -- the eyebrow is the only thread footprint (avoids repeating the summary)
    assert "story so far" not in out.lower()
    assert "still tracking" not in out.lower()


def test_eyebrow_hidden_on_first_day():
    out = render.render_article({**BASE, "thread": {"day": 1}}, slug="x")
    assert "eyebrow" not in out  # day 1 isn't "ongoing" yet


def test_eyebrow_absent_when_thread_empty():
    assert "eyebrow" not in render.render_article({**BASE, "thread": {}}, slug="x")


def test_delta_replaces_summary_for_threaded_story():
    article = {**BASE, "thread": {"day": 4, "delta": "Britain broke its all-time June record today."}}
    out = render.render_article(article, slug="x")
    assert "Britain broke its all-time June record today." in out  # delta is the rendered lede
    assert "Temperatures hit 40C across the continent." not in out  # generic WRITE summary replaced


def test_no_delta_falls_back_to_write_summary():
    out = render.render_article({**BASE, "thread": {"day": 4, "delta": ""}}, slug="x")
    assert "Temperatures hit 40C across the continent." in out  # quiet day -> normal summary
    assert '<span class="loc">Ongoing</span> · day 4' in out  # eyebrow still shown


def test_delta_is_html_escaped():
    out = render.render_article({**BASE, "thread": {"day": 2, "delta": "<script>x</script>"}}, slug="x")
    assert "<script>x" not in out
    assert "&lt;script&gt;" in out


def test_brief_uses_summary_class_and_no_why():
    out = render.render_article(BASE, slug="x", is_brief=True)
    assert '<article class="brief" id="x">' in out
    assert '<p class="summary">' in out
    assert "why" not in out  # briefs carry no why-it-matters block
    assert 'class="head"' not in out  # brief h3 has no .head class


def test_first_story_has_no_separator_others_do():
    # The first item in a section has no leading separator; the rest get a .artsep.
    assert render.render_article(BASE, slug="x", is_first=True).startswith('<article id="x">')
    assert render.render_article(BASE, slug="x").startswith('<table role="presentation" class="artsep"')
