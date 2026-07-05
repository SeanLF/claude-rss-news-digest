"""Tests for the footer-nav Subscribe link in render.replace_placeholders.

The web template carries a Subscribe link ({{SUBSCRIBE_URL}}) in the footer nav.
With a configured DIGEST_DOMAIN it resolves to the site's #subscribe anchor;
without one the link (and one adjacent " · " separator) is stripped so no
leftover placeholder ships, while the footer nav's other links survive.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from render import replace_placeholders

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head><style>{{STYLES}}</style></head>
<body>
  <h1>{{DIGEST_NAME}} - {{DATE}}</h1>
  <footer>
    <nav aria-label="Digest footer"><a href="{{ARCHIVE_URL}}">Past digests</a> · <a href="{{PRIVACY_URL}}">Privacy</a> · <a href="{{SUBSCRIBE_URL}}">Subscribe</a></nav>
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


class TestSubscribeLink:
    def test_subscribe_resolves_when_domain_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIGEST_DOMAIN", "example.com")
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        assert "https://example.com/#subscribe" in content
        assert "{{SUBSCRIBE_URL}}" not in content
        assert ">Subscribe</a>" in content

    def test_subscribe_stripped_without_domain(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIGEST_DOMAIN", raising=False)
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, {"must_know": [], "should_know": []}, styles_path)

        content = digest_path.read_text()
        # No leftover placeholder; the Subscribe link (and its separator) are gone.
        assert "{{SUBSCRIBE_URL}}" not in content
        assert "Subscribe" not in content
