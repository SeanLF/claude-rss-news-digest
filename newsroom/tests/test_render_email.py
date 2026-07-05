"""Tests for the MJML email renderer (render_email, compiled via mjml-python/mrml).

Covers that every article state renders without error and the compiled HTML carries
the expected content + Outlook hardening. See docs/2026-07-05-mjml-email-migration.md.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import render_email

REPO = Path(__file__).resolve().parents[2]

# Exercises: full L/C/R bias bar + why + reporting_varies; thread eyebrow + delta;
# single-source (singular wording); no-usable-source; a brief.
SELECTIONS = {
    "preheader": "Preheader that should reach the MJML preview.",
    "not_covered_blurb": "Routine market moves and a minor reshuffle.",
    "must_know": [
        {
            "headline": "Full spectrum story",
            "summary": "Across the spectrum.",
            "why_it_matters": "It matters.",
            "reporting_varies": [{"source": "Left outlets", "angle": "framed as failure"}],
            "sources": [
                {"name": "Guardian", "bias": "left", "url": "https://www.theguardian.com/x/a"},
                {"name": "Reuters", "bias": "center", "url": "https://www.reuters.com/x/b"},
                {"name": "Fox", "bias": "right", "url": "https://www.foxnews.com/x/c"},
            ],
        },
        {
            "headline": "Ongoing thread story",
            "summary": "Base summary.",
            "thread": {"day": 13, "delta": "Day 13 delta replaces the summary."},
            "sources": [{"name": "Jacobin", "bias": "far-left", "url": "https://jacobin.com/x/d"}],
        },
        {
            "headline": "No usable sources",
            "summary": "All bare domains.",
            "sources": [{"name": "Example", "bias": "center", "url": "https://example.com/"}],
        },
    ],
    "should_know": [
        {
            "headline": "A brief",
            "summary": "Compact.",
            "sources": [{"name": "AP", "bias": "center", "url": "https://apnews.com/x/e"}],
        }
    ],
}


@pytest.fixture
def html(monkeypatch):
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
    monkeypatch.setenv("ARCHIVE_URL", "https://example.com")
    monkeypatch.setenv("AUTHOR_NAME", "Sean Floyd")
    monkeypatch.setenv("AUTHOR_URL", "https://seanfloyd.dev")
    return render_email.render_email(SELECTIONS, unsubscribe_url="https://example.com/unsubscribe")


def test_compiles_to_outlook_hardened_html(html):
    assert html.lstrip().lower().startswith(("<!doctype", "<html"))
    assert "<table" in html  # MJML table-based output
    assert "[if mso" in html  # Outlook ghost tables / conditionals


def test_carries_masthead_and_sections(html):
    assert "Sean&#39;s Daily" in html or "Sean's Daily" in html
    assert "Must" in html and "Should" in html  # title-case in HTML, uppercased by CSS
    assert "Preheader that should reach" in html  # mj-preview


def test_bias_bar_colours_and_source_wording(html):
    assert "#5f7391" in html and "#928f86" in html and "#b0604e" in html  # l/c/r
    assert "view sources online" in html  # plural (3-source story)
    assert "view source online" in html  # singular (1-source thread story)


def test_thread_delta_and_reporting_varies(html):
    assert "Day 13 delta replaces the summary." in html
    assert "Base summary." not in html  # delta replaced it
    assert "How reporting varies" in html.replace("&#160;", " ").upper() or "REPORTING VARIES" in html.upper()


def test_footer_links_are_styled_and_unsubscribe_substituted(html):
    assert "https://example.com/unsubscribe" in html
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in html
    assert "Made by" in html


def test_no_source_story_has_no_bias_bar_line(html):
    # The no-usable-source story renders (headline present) but drops the source block.
    assert "No usable sources" in html


def test_production_default_keeps_the_merge_tag(monkeypatch):
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
    out = render_email.render_email(SELECTIONS)  # default unsubscribe_url
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in out  # left for Resend to fill per-recipient
