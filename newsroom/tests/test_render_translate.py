"""Tests for the Translate + Subscribe footer links in render.replace_placeholders.

The email template carries an email-only footer-actions line whose translate href
is {{HOMEPAGE_URL}}/translate, plus a Subscribe link ({{SUBSCRIBE_URL}}) in the
footer nav. With a configured DIGEST_DOMAIN both resolve to per-date routes;
without one the translate line and the Subscribe link are stripped (no leftover
placeholder), while the footer nav's other links survive.
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
  <footer>
    <nav aria-label="Digest footer"><a href="{{ARCHIVE_URL}}">Past digests</a> · <a href="{{PRIVACY_URL}}">Privacy</a> · <a href="{{SUBSCRIBE_URL}}">Subscribe</a> · <a href="{{{RESEND_UNSUBSCRIBE_URL}}}">Unsubscribe</a></nav>
    <p class="footer-actions email-only"><a href="{{HOMEPAGE_URL}}/translate">Read this in another language</a> · Reply with feedback</p>
    <p class="footer-meta generated-at">{{GENERATED_AT}}</p>
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


class TestTranslateAndSubscribeLines:
    def test_links_resolve_to_per_date_routes_when_domain_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        assert f"https://example.com/{_today()}/translate" in content
        assert "https://example.com/#subscribe" in content
        assert "{{HOMEPAGE_URL}}" not in content
        assert "{{SUBSCRIBE_URL}}" not in content
        # Reply text survives (it lives in the footer-actions line).
        assert "Reply with feedback" in content

    def test_translate_and_subscribe_stripped_without_domain(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIGEST_DOMAIN", raising=False)
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        # No leftover placeholders; the translate line and Subscribe link are gone.
        assert "{{HOMEPAGE_URL}}" not in content
        assert "{{SUBSCRIBE_URL}}" not in content
        assert "footer-actions" not in content
        assert "Subscribe" not in content
        # The triple-brace Resend token is left intact for send-time substitution.
        assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in content
