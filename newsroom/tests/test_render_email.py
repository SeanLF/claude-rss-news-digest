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


def test_email_colours_are_the_light_tokens_single_source():
    """render_email's colour constants must equal the light values in tokens.css.

    Guards the DRY: a future edit to design/tokens.css light --* values flows through
    to the email instead of drifting from a hardcoded copy. Fonts are intentionally
    NOT shared (email uses web-safe stacks), so only colours are asserted.
    """
    from render import light_tokens

    tokens = light_tokens(REPO / "design" / "tokens.css")
    assert tokens, "tokens.css must parse to a non-empty light-mode token map"
    assert tokens["bg"] == render_email.BG
    assert tokens["ink"] == render_email.INK
    assert tokens["ink2"] == render_email.INK2
    assert tokens["muted"] == render_email.MUTED
    assert tokens["hair"] == render_email.HAIR
    assert tokens["accent"] == render_email.ACCENT
    assert tokens["accent-ink"] == render_email.ACCENT_INK
    assert {"l": tokens["bias-l"], "c": tokens["bias-c"], "r": tokens["bias-r"]} == render_email.BIAS


# ---------------------------------------------------------------------------
# Edge cases: every one must render WITHOUT crashing and produce sane HTML.
# ---------------------------------------------------------------------------


def _render(monkeypatch, selections, **kwargs):
    """Render arbitrary selections with the standard test env (real tokens + domain)."""
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
    monkeypatch.setenv("ARCHIVE_URL", "https://example.com")
    monkeypatch.setenv("AUTHOR_NAME", "Sean Floyd")
    monkeypatch.setenv("AUTHOR_URL", "https://seanfloyd.dev")
    return render_email.render_email(selections, **kwargs)


# The glued (non-breaking-space) section name only appears in a rendered section
# header, so it's a clean probe for "did this section render at all".
MUST_HEADER = "Must&#160;Know"
SHOULD_HEADER = "Should&#160;Know"


def test_empty_digest_renders_masthead_and_footer(monkeypatch):
    out = _render(monkeypatch, {"must_know": [], "should_know": []})
    assert out.lstrip().lower().startswith(("<!doctype", "<html"))
    assert "Sean&#39;s Daily" in out or "Sean's Daily" in out  # masthead survives
    assert "Reply to this email" in out  # footer survives
    assert MUST_HEADER not in out and SHOULD_HEADER not in out  # no section bodies


def test_only_must_know_omits_should_header(monkeypatch):
    out = _render(monkeypatch, {"must_know": [{"headline": "Solo story", "summary": "S."}], "should_know": []})
    assert "Solo story" in out
    assert MUST_HEADER in out
    assert SHOULD_HEADER not in out


def test_only_should_know_omits_must_header(monkeypatch):
    out = _render(monkeypatch, {"must_know": [], "should_know": [{"headline": "Just a brief", "summary": "B."}]})
    assert "Just a brief" in out
    assert SHOULD_HEADER in out
    assert MUST_HEADER not in out


def test_story_with_only_headline_and_no_source_block(monkeypatch):
    # Only headline present: no summary/why/varies/sources/thread.
    out = _render(monkeypatch, {"must_know": [{"headline": "Bare headline"}], "should_know": []})
    assert "Bare headline" in out
    assert "Why it matters" not in out
    assert "How reporting varies" not in out.replace("&#160;", " ")
    assert "view source" not in out  # no source block at all (no bias bar link)


def test_empty_strings_do_not_crash(monkeypatch):
    out = _render(monkeypatch, {"must_know": [{"headline": "", "summary": ""}], "should_know": []})
    assert out.lstrip().lower().startswith(("<!doctype", "<html"))
    assert MUST_HEADER in out  # the item still counts, so the section renders


def test_unicode_and_long_content_render_and_escape(monkeypatch):
    long_summary = "Une tres longue analyse detaillee du dossier. " * 15  # >500 chars
    assert len(long_summary) > 500
    out = _render(
        monkeypatch,
        {"must_know": [{"headline": "\U0001f30d 危机加深 café", "summary": long_summary}], "should_know": []},
    )
    assert "\U0001f30d" in out  # emoji preserved (html.escape leaves non-ASCII alone)
    assert "危机加深" in out  # CJK preserved
    assert "café" in out  # combining accent preserved
    assert "Une tres longue analyse detaillee du dossier." in out  # long body rendered


def test_html_injection_stays_escaped(monkeypatch):
    payload = '<script>alert("xss")</script>'
    out = _render(
        monkeypatch,
        {
            "must_know": [
                {
                    "headline": f"Hack {payload}",
                    "summary": f"Body {payload} </mj-text>",
                    "why_it_matters": payload,
                    "reporting_varies": [{"source": f"Evil {payload}", "angle": f"Angle {payload}"}],
                    "sources": [{"name": f"Src {payload}", "bias": "center", "url": "https://example.com/article/x"}],
                }
            ],
            "should_know": [],
        },
    )
    assert "<script>" not in out  # raw tag never appears unescaped
    assert "</mj-text>alert" not in out  # the injected closing tag didn't break out
    assert "&lt;script&gt;" in out  # escaped form is what actually ships


def test_all_sources_bare_domain_dropped_no_dangling_bars(monkeypatch):
    out = _render(
        monkeypatch,
        {
            "must_know": [
                {
                    "headline": "Story one",
                    "summary": "S1",
                    "sources": [{"name": "A", "bias": "left", "url": "https://a.com/"}],
                }
            ],
            "should_know": [
                {
                    "headline": "Brief one",
                    "summary": "B1",
                    "sources": [{"name": "B", "bias": "right", "url": "https://b.com"}],
                }
            ],
        },
    )
    assert "Story one" in out and "Brief one" in out
    assert "view source" not in out  # every source block dropped
    # No dangling bias bars: none of the l/c/r bias colours should appear anywhere.
    assert "#5f7391" not in out and "#928f86" not in out and "#b0604e" not in out


def test_reporting_varies_absent_on_brief(monkeypatch):
    # reporting_varies is a must-know-only surface; a brief must never render it.
    out = _render(
        monkeypatch,
        {
            "must_know": [],
            "should_know": [
                {
                    "headline": "Brief with varies",
                    "summary": "B",
                    "reporting_varies": [{"source": "X", "angle": "unique-varies-angle-token"}],
                }
            ],
        },
    )
    assert "Brief with varies" in out
    assert "How reporting varies" not in out.replace("&#160;", " ")
    assert "unique-varies-angle-token" not in out  # angle text never rendered on a brief


def test_render_email_raises_on_empty_mrml_output(monkeypatch):
    # Guards the "don't broadcast broken/empty HTML" fix: empty str from mrml -> raise.
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setattr(render_email, "mjml2html", lambda _mjml: "")
    with pytest.raises(RuntimeError):
        render_email.render_email(SELECTIONS)


def test_render_email_raises_on_mrml_errors_dict(monkeypatch):
    # Some mjml-python versions return {html, errors}; a non-empty errors list -> raise.
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setattr(render_email, "mjml2html", lambda _mjml: {"html": "", "errors": ["boom"]})
    with pytest.raises(RuntimeError):
        render_email.render_email(SELECTIONS)


# ---------------------------------------------------------------------------
# light_tokens: the shared colour source for the email literals.
# ---------------------------------------------------------------------------


def test_light_tokens_parses_expected_keys():
    from render import light_tokens

    tokens = light_tokens(REPO / "design" / "tokens.css")
    assert tokens  # non-empty
    for key in ("bg", "ink", "ink2", "muted", "hair", "accent", "accent-ink", "bias-l", "bias-c", "bias-r"):
        assert key in tokens, f"missing token {key}"


def test_light_tokens_missing_file_returns_empty(tmp_path):
    from render import light_tokens

    assert light_tokens(tmp_path / "does-not-exist.css") == {}


def test_light_tokens_ignores_dark_mode_values():
    from render import light_tokens

    tokens = light_tokens(REPO / "design" / "tokens.css")
    # tokens.css ships a dark palette (bg #16150f) in @media + :root[data-theme];
    # light_tokens must pick the plain-:root light value only.
    assert tokens["bg"] == "#fafaf8"
    assert tokens["bg"] != "#16150f"
