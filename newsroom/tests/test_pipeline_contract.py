"""End-to-end contract between the digest template and its two consumers.

The template is rendered ONCE, then transformed two ways that must stay in sync:
  - newsroom `prepare_for_email`: resolve vars to light, inline, strip web-only.
  - circulation serves the `prepare_for_web` blob and injects its web chrome by
    string-matching a fixed set of needles -- and only logs a warning on a miss,
    so a renamed class silently ships an archive page with no topbar / toggle /
    skip link. Nothing else in CI renders the REAL template through both paths, so
    the two sides can drift arbitrarily and stay green. This test is that guard.

If you intentionally rename one of these structural hooks, update BOTH the
template and circulation/src/handlers.rs, then this test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import db
import render

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "newsroom" / "templates" / "digest-template.html"
STYLES = REPO / "newsroom" / "templates" / "digest.css"

# The exact substrings circulation/src/handlers.rs `inject(...)` targets to splice
# in its web chrome. Keep in lockstep with those calls -- this list is the whole
# point of the test. These four have NO fallback: a miss silently drops chrome.
CIRCULATION_HARD_NEEDLES = [
    "</head>",  # head bundle (color-scheme, favicon, nav CSS, font-face, toggle CSS)
    "<body>",  # skip link
    '<div class="paper">',  # top utility bar (topbar)
    "</body>",  # theme-toggle JS
]
# The feedback line injects at <p class="footer-meta"> OR falls back to </footer>,
# so the contract is that at least one is present.
CIRCULATION_FEEDBACK_NEEDLE = '<p class="footer-meta">'
CIRCULATION_FEEDBACK_FALLBACK = "</footer>"

SELECTIONS = {
    "preheader": "Contract-test preheader that should survive into the email head.",
    "must_know": [
        {
            "headline": "Contract test story with a source",
            "summary": "One source so the source block renders on both channels.",
            "why_it_matters": "Exercises the why-it-matters block.",
            "sources": [{"name": "Reuters", "bias": "center", "url": "https://www.reuters.com/world/contract-test"}],
        }
    ],
    "should_know": [
        {
            "headline": "Contract test brief",
            "summary": "A compact brief with one left-leaning source.",
            "sources": [
                {
                    "name": "The Guardian",
                    "bias": "left",
                    "url": "https://www.theguardian.com/world/2026/jul/05/contract-brief",
                }
            ],
        }
    ],
}


@pytest.fixture
def rendered(tmp_path, monkeypatch):
    """The real template + CSS, fully placeholder-filled (domain configured so the
    email-only view-in-browser/unsubscribe surfaces are present, not stripped)."""
    monkeypatch.setattr(config, "TOKENS_FILE", REPO / "design" / "tokens.css")
    monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
    monkeypatch.setenv("ARCHIVE_URL", "https://example.com")
    monkeypatch.setenv("AUTHOR_URL", "https://seanfloyd.dev")
    monkeypatch.setenv("AUTHOR_NAME", "Sean Floyd")
    path = tmp_path / "digest-2026-07-05.html"
    path.write_text(render.render_digest(SELECTIONS, TEMPLATE))
    render.replace_placeholders(path, SELECTIONS, STYLES, preheader=SELECTIONS["preheader"])
    return path.read_text()


class TestEmailPrep:
    def test_strips_web_only_source_table(self, rendered):
        email = render.prepare_for_email(rendered)
        assert 'class="srcbox' not in email  # the web-only <details> source table
        assert "<details" not in email

    def test_declares_light_only_color_scheme(self, rendered):
        email = render.prepare_for_email(rendered)
        assert '<meta name="color-scheme" content="light">' in email
        assert '<meta name="supported-color-schemes" content="light">' in email

    def test_no_root_or_data_theme_blocks_leak(self, rendered):
        email = render.prepare_for_email(rendered)
        assert ":root" not in email  # resolved + stripped, incl. :root[data-theme]
        assert "data-theme" not in email

    def test_outlook_width_wrapper_survives_inlining(self, rendered):
        email = render.prepare_for_email(rendered)
        assert "[if mso]" in email  # MSO-only width table survived premailer

    def test_no_unresolved_placeholders(self, rendered):
        import re

        email = render.prepare_for_email(rendered)
        scan = re.sub(r"<style>.*?</style>", "", email, flags=re.DOTALL)
        scan = re.sub(r"\{\{\{[^{}]+\}\}\}", "", scan)  # Resend per-recipient merge tags
        assert re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", scan) is None


class TestWebPrep:
    def test_all_circulation_injection_needles_present(self, rendered):
        web = db.prepare_for_web(rendered)
        missing = [n for n in CIRCULATION_HARD_NEEDLES if n not in web]
        assert not missing, f"circulation can no longer inject at: {missing}"
        # Feedback line: primary needle OR its documented fallback.
        assert CIRCULATION_FEEDBACK_NEEDLE in web or CIRCULATION_FEEDBACK_FALLBACK in web

    def test_skip_link_target_present(self, rendered):
        web = db.prepare_for_web(rendered)
        assert 'id="main"' in web  # circulation's skip link points at #main

    def test_email_only_content_removed(self, rendered):
        web = db.prepare_for_web(rendered)
        assert 'class="email-only"' not in web
        assert 'class="webview' not in web  # view-in-browser line (the element, not its CSS rule)
        assert "RESEND_UNSUBSCRIBE_URL" not in web  # per-recipient merge tag never on the web
        assert 'class="srcline' not in web  # the email static source line
        assert 'class="preheader"' not in web  # inbox-preview text/spacer
        assert "[if mso" not in web  # Outlook wrapper is email-only noise

    def test_web_only_content_survives_for_the_flip(self, rendered):
        web = db.prepare_for_web(rendered)
        assert 'class="web-only"' in web  # footer Subscribe, revealed by circulation's flip
        assert 'class="srcbox' in web  # the source <details> the flip reveals


def test_flip_class_names_are_the_contract(rendered):
    """render.py emits exactly `email-only`/`web-only`; circulation's DIGEST_NAV_CSS
    flips those same names. If either side renames them, the archive silently shows
    email-only content or hides web-only content."""
    assert "email-only" in rendered
    assert "web-only" in rendered
