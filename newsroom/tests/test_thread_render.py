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
    assert "thread-ledger" not in out
    assert "European heatwave breaks records" in out


def test_render_article_with_continuing_thread_shows_badge_and_ledger():
    article = {
        **BASE,
        "thread": {
            "day": 4,
            "open_questions": ["How long will the heat dome persist?", "Will records fall in the UK?"],
            "resolved": ["How many have died in France?"],
        },
    }
    out = render.render_article(article, slug="heatwave")
    assert 'class="thread-badge">Ongoing · day 4' in out
    assert "Still tracking:" in out
    assert "How long will the heat dome persist?" in out
    assert "Now answered:" in out
    assert "How many have died in France?" in out


def test_thread_badge_hidden_on_first_day():
    article = {**BASE, "thread": {"day": 1, "open_questions": ["Q?"], "resolved": []}}
    out = render.render_article(article, slug="x")
    assert "thread-badge" not in out  # day 1 isn't "ongoing" yet
    assert "Still tracking:" in out  # but the ledger still shows


def test_thread_ledger_empty_when_no_questions():
    assert render._render_thread_ledger({"day": 3, "open_questions": [], "resolved": []}) == ""


def test_thread_ledger_caps_open_questions():
    ledger = render._render_thread_ledger(
        {"day": 2, "open_questions": [f"q{i}" for i in range(6)], "resolved": []}, max_open=3
    )
    assert ledger.count(" · ") == 2  # 3 questions -> 2 separators


def test_thread_ledger_escapes_html():
    out = render._render_thread_ledger({"day": 2, "open_questions": ["<script>x</script>"], "resolved": []})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
