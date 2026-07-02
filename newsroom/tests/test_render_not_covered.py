"""Tests for the not_covered_blurb footer garnish in render.replace_placeholders.

The digest template carries a bare {{NOT_COVERED}} placeholder inside a
<p class="not-covered"> line. When selections.json has a usable
not_covered_blurb, that line is filled in; when absent, the whole <p> is
stripped so no empty markup ships (same pattern as {{AUTHOR_PLUG}}).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from render import replace_placeholders

MINIMAL_TEMPLATE = """<!DOCTYPE html>
<html>
<head><style>{{STYLES}}</style></head>
<body>
  <span class="preheader">{{PREHEADER}}</span>
  <h1>{{DIGEST_NAME}} - {{DATE}}</h1>
  <p>{{READING_TIME}} - {{STORY_COUNT}}</p>
  <footer>
    <p class="not-covered">{{NOT_COVERED}}</p>
    <p class="generated-at">{{GENERATED_AT}}</p>
  </footer>
</body>
</html>
"""


def _write_digest(tmp_path, template=MINIMAL_TEMPLATE):
    digest_path = tmp_path / "digest.html"
    digest_path.write_text(template)
    styles_path = tmp_path / "styles.css"
    styles_path.write_text("body { color: black; }")
    return digest_path, styles_path


class TestNotCoveredFooter:
    def test_renders_line_when_blurb_present(self, tmp_path):
        digest_path, styles_path = _write_digest(tmp_path)
        selections = {"must_know": [], "should_know": [], "not_covered_blurb": "Skipped celebrity gossip."}

        replace_placeholders(digest_path, selections, styles_path)

        content = digest_path.read_text()
        assert '<p class="not-covered">Not covered today: Skipped celebrity gossip.</p>' in content
        assert "{{NOT_COVERED}}" not in content

    @pytest.mark.parametrize(
        "selections",
        [
            {"must_know": [], "should_know": []},
            {"must_know": [], "should_know": [], "not_covered_blurb": "   "},
        ],
        ids=["field-missing", "blank-string"],
    )
    def test_omits_markup_when_blurb_missing_or_blank(self, tmp_path, selections):
        digest_path, styles_path = _write_digest(tmp_path)

        replace_placeholders(digest_path, selections, styles_path)

        content = digest_path.read_text()
        assert "not-covered" not in content
        assert "{{NOT_COVERED}}" not in content

    def test_blurb_is_html_escaped(self, tmp_path):
        digest_path, styles_path = _write_digest(tmp_path)
        selections = {"must_know": [], "should_know": [], "not_covered_blurb": "<script>x</script>"}

        replace_placeholders(digest_path, selections, styles_path)

        content = digest_path.read_text()
        assert "<script>x" not in content
        assert "&lt;script&gt;" in content

    def test_warns_when_blurb_present_but_wrong_type(self, tmp_path, caplog):
        # Matters most in --write-only re-renders: merge.py already ran (or
        # was skipped entirely, for a hand-edited selections.json), so this
        # is the only place left to surface that the footer line got dropped.
        digest_path, styles_path = _write_digest(tmp_path)
        selections = {"must_know": [], "should_know": [], "not_covered_blurb": ["not", "a", "string"]}

        with caplog.at_level("WARNING", logger="render"):
            replace_placeholders(digest_path, selections, styles_path)

        content = digest_path.read_text()
        assert "not-covered" not in content
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("not_covered_blurb" in r.getMessage() and "list" in r.getMessage() for r in warnings)
