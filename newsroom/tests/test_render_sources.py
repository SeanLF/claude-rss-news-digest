"""Tests for the digest sources block: bias bucketing, the bias bar, the spread
label, per-outlet grouping with numbered links, URL safety, escaping -- and the
residual-placeholder sweep in replace_placeholders.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from render import _bias_bucket, prepare_for_email, render_article, replace_placeholders

# ---------------------------------------------------------------------------
# Bias bucketing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bias,bucket",
    [
        ("left", "l"),
        ("lean-left", "l"),
        ("far-left", "l"),
        ("LEAN-LEFT", "l"),
        ("right", "r"),
        ("lean-right", "r"),
        ("far-right", "r"),
        ("center", "c"),
        ("lean-center", "c"),
        ("", "c"),
        ("unknown", "c"),
    ],
)
def test_bias_bucket_mapping(bias, bucket):
    assert _bias_bucket(bias) == bucket


# ---------------------------------------------------------------------------
# Bias bar + spread label
# ---------------------------------------------------------------------------


def _article(sources):
    return {"headline": "H", "summary": "S", "sources": sources}


def test_bias_bar_emits_only_nonempty_buckets_in_lcr_order():
    # Only left + center present -> two segs, l before c, no r seg, no "0 right".
    out = render_article(
        _article(
            [
                {"name": "AJ", "bias": "lean-left", "url": "https://aj.com/a"},
                {"name": "BBC", "bias": "center", "url": "https://bbc.com/b"},
                {"name": "Reuters", "bias": "center", "url": "https://reuters.com/c"},
            ]
        ),
        slug="x",
    )
    # left seg comes before center seg; no right seg
    assert '<span class="seg l" style="flex:1">' in out
    assert '<span class="seg c" style="flex:2">' in out
    assert '"seg r"' not in out
    assert out.index('seg l"') < out.index('seg c"')
    assert "2 left" not in out  # left has 1
    assert "1 left · 2 center" in out
    assert "right" not in out.split("spread-label")[1][:80]


def test_spread_label_singularizes_source_word():
    single = render_article(_article([{"name": "AJ", "bias": "left", "url": "https://aj.com/a"}]), slug="x")
    assert "1 source" in single
    assert "1 sources" not in single
    assert "view all 1 source online" in single

    multi = render_article(
        _article(
            [
                {"name": "AJ", "bias": "left", "url": "https://aj.com/a"},
                {"name": "BBC", "bias": "center", "url": "https://bbc.com/b"},
            ]
        ),
        slug="x",
    )
    assert "2 sources" in multi
    assert "view all 2 sources online" in multi


# ---------------------------------------------------------------------------
# Per-outlet grouping, ordering, numbered links, URL safety, escaping
# ---------------------------------------------------------------------------


def test_sources_grouped_per_outlet_with_numbered_links():
    out = render_article(
        _article(
            [
                {"name": "NYT", "bias": "lean-left", "url": "https://nyt.com/1"},
                {"name": "NYT", "bias": "lean-left", "url": "https://nyt.com/2"},
                {"name": "NYT", "bias": "lean-left", "url": "https://nyt.com/3"},
            ]
        ),
        slug="x",
    )
    # One row for NYT, three numbered links.
    assert out.count('<td class="nm">NYT</td>') == 1
    assert '<a href="https://nyt.com/1">1</a>' in out
    assert '<a href="https://nyt.com/2">2</a>' in out
    assert '<a href="https://nyt.com/3">3</a>' in out
    # Single outlet with 3 URLs is still "1 source" (count is per-outlet).
    assert "1 source" in out


def test_rows_ordered_left_center_right():
    out = render_article(
        _article(
            [
                {"name": "Globe", "bias": "lean-right", "url": "https://globe.com/a"},
                {"name": "BBC", "bias": "center", "url": "https://bbc.com/b"},
                {"name": "AJ", "bias": "lean-left", "url": "https://aj.com/c"},
            ]
        ),
        slug="x",
    )
    assert out.index('"nm">AJ<') < out.index('"nm">BBC<') < out.index('"nm">Globe<')


def test_unsafe_and_bare_domain_urls_dropped():
    out = render_article(
        _article(
            [
                {"name": "Good", "bias": "center", "url": "https://good.com/story"},
                {"name": "Evil", "bias": "center", "url": "javascript:alert(1)"},
                {"name": "Bare", "bias": "center", "url": "https://bare.com/"},
            ]
        ),
        slug="x",
    )
    assert "javascript:alert(1)" not in out
    # Outlets whose only URL failed the guards are dropped entirely.
    assert "Evil" not in out
    assert "Bare" not in out
    assert '<td class="nm">Good</td>' in out
    assert "1 source" in out  # only the one openable outlet counts


def test_source_text_is_html_escaped():
    out = render_article(
        _article([{"name": "A&B <News>", "bias": "left", "url": "https://ab.com/x?a=1&b=2"}]),
        slug="x",
    )
    assert "A&amp;B &lt;News&gt;" in out
    assert "<News>" not in out
    assert "a=1&amp;b=2" in out


def test_no_sources_block_when_all_urls_unusable():
    out = render_article(
        _article([{"name": "Bare", "bias": "center", "url": "https://bare.com/"}]),
        slug="x",
    )
    assert "srcbox" not in out
    assert "spread" not in out


def test_email_line_carries_archive_placeholder_for_view_all_link():
    out = render_article(_article([{"name": "AJ", "bias": "left", "url": "https://aj.com/a"}]), slug="my-slug")
    assert '<a href="{{ARCHIVE_URL}}#my-slug">view all 1 source online</a>' in out


# ---------------------------------------------------------------------------
# Reporting varies (must-know only)
# ---------------------------------------------------------------------------


def test_reporting_varies_rendered_for_story_not_brief():
    article = {
        "headline": "H",
        "summary": "S",
        "sources": [{"name": "AJ", "bias": "left", "url": "https://aj.com/a"}],
        "reporting_varies": [{"source": "Reuters", "angle": "Reported toll of 21."}],
    }
    story = render_article(article, slug="x")
    assert '<div class="varies"><span class="lbl">How reporting varies</span>' in story
    assert "<b>Reuters:</b> Reported toll of 21." in story

    brief = render_article(article, slug="x", is_brief=True)
    assert "varies" not in brief  # briefs never show reporting-varies


# ---------------------------------------------------------------------------
# Residual-placeholder sweep
# ---------------------------------------------------------------------------

# NB: keep the double-brace placeholders literal -- do NOT use str.format (it
# would collapse {{X}} to {X}). The EXTRA_SLOT sentinel is swapped via .replace.
MIN_TEMPLATE = (
    "<!DOCTYPE html><html><head><style>{{STYLES}}</style></head>"
    "<body><h1>{{DIGEST_NAME}} - {{DATE}}</h1>EXTRA_SLOT</body></html>"
)


def _prep(tmp_path, extra):
    digest = tmp_path / "digest.html"
    digest.write_text(MIN_TEMPLATE.replace("EXTRA_SLOT", extra))
    styles = tmp_path / "styles.css"
    styles.write_text("body{color:#000}")
    return digest, styles


def test_residual_sweep_raises_on_leftover_placeholder(tmp_path):
    digest, styles = _prep(tmp_path, extra="<p>{{FOO_BAR}}</p>")
    with pytest.raises(RuntimeError, match=r"\{\{FOO_BAR\}\}"):
        replace_placeholders(digest, {"must_know": [], "should_know": []}, styles)


def test_residual_sweep_allows_resend_triple_brace(tmp_path):
    digest, styles = _prep(tmp_path, extra='<a href="{{{RESEND_UNSUBSCRIBE_URL}}}">Unsubscribe</a>')
    # Must NOT raise: Resend fills the triple-brace token per-recipient at send time.
    replace_placeholders(digest, {"must_know": [], "should_know": []}, styles)
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in digest.read_text()


def test_residual_sweep_ignores_css_braces(tmp_path):
    # Minified CSS with adjacent braces must not trip the sweep.
    digest = tmp_path / "digest.html"
    digest.write_text(MIN_TEMPLATE.replace("EXTRA_SLOT", "<p>ok</p>"))
    styles = tmp_path / "styles.css"
    styles.write_text("@media (min-width:760px){.a{color:red}}")
    replace_placeholders(digest, {"must_know": [], "should_know": []}, styles)
    assert "color:red" in digest.read_text()


def test_residual_sweep_ignores_editorial_prose_braces(tmp_path):
    # html.escape does NOT escape { } -- an article quoting CI/template syntax must not
    # abort the whole send. Only {{UPPER_SNAKE}} is a real placeholder.
    prose = "<p>the workflow leaked ${{ secrets.TOKEN }} and the Vue tag {{ user.name }}</p>"
    digest, styles = _prep(tmp_path, extra=prose)
    replace_placeholders(digest, {"must_know": [], "should_know": []}, styles)
    out = digest.read_text()
    assert "{{ secrets.TOKEN }}" in out and "{{ user.name }}" in out


def test_residual_sweep_allows_any_triple_brace_tag(tmp_path):
    # Generic {{{NAME}}} exclusion: a second Resend merge tag must not false-crash.
    digest, styles = _prep(tmp_path, extra="<p>Hi {{{FIRST_NAME}}}</p>")
    replace_placeholders(digest, {"must_know": [], "should_know": []}, styles)
    assert "{{{FIRST_NAME}}}" in digest.read_text()


def test_prepare_for_email_strips_web_only_source_table():
    # Gmail unwraps <details> and drops the display:none, leaking the source table.
    # prepare_for_email must remove the web-only block outright; the email-only
    # static line stays.
    html = (
        "<html><head><style>body{color:#000}</style></head><body>"
        '<details class="srcbox web-only"><summary>x</summary>'
        "<table><tr><td>LEAKED_TABLE</td></tr></table></details>"
        '<div class="spread email-only">3 sources &middot; view all 3 sources online</div>'
        "</body></html>"
    )
    out = prepare_for_email(html)
    assert "LEAKED_TABLE" not in out  # web-only <details> physically removed
    assert "view all 3 sources online" in out  # email-only static line kept
