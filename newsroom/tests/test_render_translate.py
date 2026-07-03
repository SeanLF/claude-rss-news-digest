"""Tests for the Translate link + suggestion-box lines in render.replace_placeholders.

The email template carries an email-only footer translate line
(<p class="footer-translate">) whose href is {{HOMEPAGE_URL}}/translate, plus a
"just hit reply" suggestion line. With a configured DIGEST_DOMAIN the translate
href resolves to the per-date /translate route; without one the translate line is
stripped (no leftover placeholder), while the reply line -- which needs no URL --
always survives.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from render import replace_placeholders

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head><style>{{STYLES}}</style></head>
<body>
  <h1>{{DIGEST_NAME}} - {{DATE}}</h1>
  <nav class="header-links">
    <a href="{{SUBSCRIBE_URL}}">Subscribe</a>
    <a href="{{HOMEPAGE_URL}}">View online</a>
    <a href="{{HOMEPAGE_URL}}/translate"><span aria-hidden="true">文A</span> Translate</a>
  </nav>
  <footer>
    <p class="footer-translate email-only"><a href="{{HOMEPAGE_URL}}/translate">Read this digest in another language →</a></p>
    <p class="footer-suggest email-only">Got feedback or a suggestion? Just hit reply.</p>
    <p class="generated-at">{{GENERATED_AT}}</p>
  </footer>
</body>
</html>
"""


def _write_digest(tmp_path):
    digest_path = tmp_path / "digest.html"
    digest_path.write_text(TEMPLATE)
    styles_path = tmp_path / "styles.css"
    styles_path.write_text("body { color: black; }")
    return digest_path, styles_path


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class TestTranslateAndFeedbackLines:
    def test_translate_href_resolves_to_per_date_route_when_domain_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        assert f"https://example.com/{_today()}/translate" in content
        assert "{{HOMEPAGE_URL}}" not in content
        # Reply line survives; no URL to resolve.
        assert "Just hit reply." in content

    def test_translate_line_stripped_but_reply_survives_without_domain(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIGEST_DOMAIN", raising=False)
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        # No leftover placeholder, and the whole translate footer line is gone.
        assert "{{HOMEPAGE_URL}}" not in content
        assert "footer-translate" not in content
        # Header nav (which held the header translate link) is stripped too.
        assert "header-links" not in content
        # The reply suggestion has no URL, so it always ships.
        assert "Just hit reply." in content
