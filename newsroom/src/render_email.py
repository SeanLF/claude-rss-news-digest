"""Email rendering via MJML (mjml-python / mrml -- native Python, no Node).

Separate from render.py's web rendering: the web serves clean semantic HTML; the
email is authored as MJML and compiled to Outlook-hardened tables by mrml. Both are
driven by the same ``selections`` data + shared design tokens. This is the sole
email renderer -- the old premailer ``prepare_for_email`` path has been removed.

See docs/2026-07-05-mjml-email-migration.md.
"""

import html
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import db
from mjml import mjml2html
from render import (
    _BUCKET_ORDER,
    _BUCKET_WORD,
    _collect_outlets,
    calculate_reading_time,
    extract_preheader,
    format_story_counts,
    light_tokens,
    slugify,
)

logger = logging.getLogger(__name__)

# Colours are derived from the light-mode values in design/tokens.css (the single
# source of truth shared with the web CSS) so email literals can't drift. Fonts are
# NOT shared: email needs web-safe stacks (Georgia/Arial/Courier), not the web's
# Source Serif etc. -- custom web fonts are unreliable in email.
#
# config.TOKENS_FILE (/app/design/tokens.css) is the canonical path and exists in
# the container. On a dev host / CI running tests outside the image that path is
# absent, so fall back to the repo-root copy resolved relative to this file
# (<repo>/newsroom/src/render_email.py -> parents[2] == <repo>) -- keeping the
# module importable everywhere with REAL token values rather than KeyError-ing.
# Production always resolves via the config path above.
_T = light_tokens() or light_tokens(Path(__file__).resolve().parents[2] / "design" / "tokens.css")
BG = _T["bg"]  # #fafaf8
INK = _T["ink"]  # #191917
INK2 = _T["ink2"]  # #3b3a36
MUTED = _T["muted"]  # #6f6d65
HAIR = _T["hair"]  # #dddcd4
ACCENT = _T["accent"]  # #b1352a
ACCENT_INK = _T["accent-ink"]  # #8f2a20
BIAS = {"l": _T["bias-l"], "c": _T["bias-c"], "r": _T["bias-r"]}  # #5f7391 / #928f86 / #b0604e
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "Arial, Helvetica, sans-serif"
MONO = "'Courier New', monospace"
SIDE = "28px"  # matches .paper horizontal padding
MONO_UP = 'letter-spacing="1px" text-transform="uppercase"'  # mj-text attrs for mono meta

# The common mj-text attribute values, pushed into <mj-attributes> so each _txt only
# emits the attrs that DIFFER from these. font-family's default comes from <mj-all>
# (SERIF); the rest from an <mj-text> defaults block built from this same map. One
# source for both the defaults block and _txt's omit-if-equal check means they can't
# drift -- a mismatch would silently change the compiled email.
_TXT_DEFAULTS = {
    "font-family": SERIF,
    "font-size": "18px",
    "color": INK2,
    "line-height": "1.6",
    "align": "left",
    "padding": "0",
}


def _txt(
    content: str, *, size=18, color=INK2, font=SERIF, lh="1.6", weight=None, align="left", padding="0", extra=""
) -> str:
    vals = {
        "font-family": font,
        "font-size": f"{size}px",
        "color": color,
        "line-height": lh,
        "align": align,
        "padding": padding,
    }
    attrs = " ".join(f'{k}="{v}"' for k, v in vals.items() if v != _TXT_DEFAULTS[k])
    if weight:
        attrs += f' font-weight="{weight}"'
    if extra:
        attrs += f" {extra}"
    return f"<mj-text {attrs}>{content}</mj-text>"


def _link(url: str, text: str, color: str = ACCENT_INK) -> str:
    # Email clients don't inherit link colour from the parent -- style every <a> inline.
    return f'<a href="{url}" style="color:{color};">{text}</a>'


def _eyebrow(label: str) -> str:
    return _txt(
        label,
        size=10,
        color=ACCENT_INK,
        font=MONO,
        weight="600",
        padding="0 0 4px",
        extra='letter-spacing="1px" text-transform="uppercase"',
    )


def _section(inner: str, *, padding=f"0 {SIDE}", border_left=False) -> str:
    col = (
        f'<mj-column padding="0 0 0 16px" border-left="2px solid {ACCENT}">{inner}</mj-column>'
        if border_left
        else f"<mj-column>{inner}</mj-column>"
    )
    return f'<mj-section padding="{padding}">{col}</mj-section>'


def _section_header(num: str, name: str, gap: bool = False) -> str:
    glued = name.replace(" ", "&#160;")  # never word-wraps
    label = (
        f'<span style="font-family:{MONO};font-size:12px;font-weight:600;color:{ACCENT_INK};letter-spacing:.08em;">{num}</span>'
        f'<span style="font-family:{SANS};font-size:11px;font-weight:700;letter-spacing:.18em;'
        f'text-transform:uppercase;color:{INK};">&#160;&#160;{glued}</span>'
    )
    # Label cell FIXED at 150px, rule cell fills the rest. Fixed width both stops
    # Outlook squeezing the label to zero (which stacked it vertically) AND stops the
    # column shrinking below the text on narrow screens (which char-wrapped the name
    # to "MUST KN"/"OW"). The rule cell has NO width -- a %-width rule cell was the
    # thing that starved the label.
    rows = (
        f'<tr><td width="150" style="width:150px;white-space:nowrap;vertical-align:middle;">{label}</td>'
        f'<td style="vertical-align:middle;"><div style="height:1px;line-height:1px;font-size:0;background:{INK};">&#160;</div></td></tr>'
    )
    top = "48px" if gap else "16px"  # a new section (should-know) gets a section break
    return (
        f'<mj-section padding="{top} {SIDE} 0"><mj-column>'
        f'<mj-table cellpadding="0" cellspacing="0" width="100%" padding="0">{rows}</mj-table>'
        "</mj-column></mj-section>"
    )


def _separator(is_brief: bool) -> str:
    top, bot = ("16px", "16px") if is_brief else ("32px", "24px")
    return (
        f'<mj-section padding="0 {SIDE}"><mj-column>'
        f'<mj-spacer height="{top}" />'
        f'<mj-divider border-width="1px" border-color="{HAIR}" padding="0" />'
        f'<mj-spacer height="{bot}" />'
        "</mj-column></mj-section>"
    )


def _sources(article: dict, slug: str, homepage_url: str) -> str:
    outlets = _collect_outlets(article)
    if not outlets:
        return ""
    counts = {"l": 0, "c": 0, "r": 0}
    for o in outlets:
        counts[o["bucket"]] += 1
    total = len(outlets)
    src_word = "source" if total == 1 else "sources"
    cells = "".join(
        f'<td height="4" width="{round(100 * counts[b] / total)}%" bgcolor="{BIAS[b]}" '
        f'style="font-size:0;line-height:0;"></td>'
        for b in _BUCKET_ORDER
        if counts[b]
    )
    bar = f'<mj-table width="120px" cellpadding="0" cellspacing="0" padding="0 0 8px"><tr>{cells}</tr></mj-table>'
    buckets = " · ".join(f"{counts[b]} {_BUCKET_WORD[b]}" for b in _BUCKET_ORDER if counts[b])
    link = f"{homepage_url}#{slug}" if homepage_url else f"#{slug}"
    label = (
        f'<span style="color:{INK2};">{total} {src_word}</span> · {buckets} · '
        f'<a href="{html.escape(link)}" style="font-family:{SANS};font-size:12px;'
        f'text-transform:none;letter-spacing:0;color:{ACCENT_INK};">view {src_word} online</a>'
    )
    text = _txt(label, size=10, color=MUTED, font=MONO, extra='letter-spacing="1px" text-transform="uppercase"')
    return f'<mj-section padding="16px {SIDE} 0"><mj-column>{bar}{text}</mj-column></mj-section>'


def _eyebrow_thread(thread: dict, pad: str = "0 0 12px") -> str:
    """The "Ongoing · day N" status line -- a bare mj-text concatenated BELOW the
    headline, INSIDE the headline's column (matches the web render + design-system:
    the eyebrow reads like a dateline under the head, not above it). Empty string for
    non-threads / day 1. ``pad`` is the mj-text bottom padding (12px under a story
    headline, 4px under the tighter brief headline)."""
    if thread.get("day", 0) < 2:
        return ""
    return _txt(
        f'<span style="color:{ACCENT_INK};font-weight:600;">Ongoing</span> · day {thread["day"]}',
        size=11,
        color=MUTED,
        font=MONO,
        padding=pad,
        extra='letter-spacing="1px" text-transform="uppercase"',
    )


def _story(article: dict, slug: str, homepage_url: str) -> str:
    headline = html.escape(article.get("headline", ""))
    summary = html.escape(article.get("summary", ""))
    thread = article.get("thread") or {}
    delta = (thread.get("delta") or "").strip()
    body = html.escape(delta) if delta else summary
    why = html.escape(article.get("why_it_matters", "")).strip()

    # Order matches the mockup: headline -> eyebrow -> lede (the eyebrow sits under
    # the head like a dateline). Headline + eyebrow + body share one column so the
    # eyebrow stays glued to the headline, not floated into its own section above it.
    parts = [
        _section(
            _txt(headline, size=25, color=INK, lh="1.24", weight="600", padding="0 0 12px")
            + _eyebrow_thread(thread)
            + _txt(body, color=INK),
            padding=f"24px {SIDE} 0",
        )
    ]
    if why:
        parts.append(_section(_eyebrow("Why it matters") + _txt(why), padding=f"16px {SIDE} 0", border_left=True))
    varies = article.get("reporting_varies", [])
    if varies:
        rows = "".join(
            _txt(
                f'<b style="color:{INK};">{html.escape(v.get("source", ""))}:</b> {html.escape(v.get("angle", ""))}',
                padding="8px 0 0",
            )
            for v in varies
        )
        parts.append(_section(_eyebrow("How reporting varies") + rows, padding=f"16px {SIDE} 0"))
    parts.append(_sources(article, slug, homepage_url))
    return "".join(parts)


def _brief(article: dict, slug: str, homepage_url: str, is_first: bool = False) -> str:
    headline = html.escape(article.get("headline", ""))
    summary = html.escape(article.get("summary", ""))
    thread = article.get("thread") or {}
    delta = (thread.get("delta") or "").strip()
    body = html.escape(delta) if delta else summary
    # headline -> eyebrow -> summary, same order as the story tier and the mockup
    # (mockup `.brief .eyebrow{margin-bottom:4px}` -> the eyebrow gets a 4px bottom pad).
    inner = (
        _txt(headline, size=20, color=INK, lh="1.3", weight="600", padding="0 0 4px")
        + _eyebrow_thread(thread, pad="0 0 4px")
        + _txt(body)
    )
    # The first brief needs a top gap from the section header (later briefs get it
    # from the separator); stories already carry their own top padding.
    pad = f"20px {SIDE} 0" if is_first else f"0 {SIDE}"
    return _section(inner, padding=pad) + _sources(article, slug, homepage_url)


def render_email(selections: dict, unsubscribe_url: str = "{{{RESEND_UNSUBSCRIBE_URL}}}") -> str:
    """Render selections to Outlook-hardened email HTML via MJML/mrml."""
    now = datetime.now(UTC)
    date_str = now.strftime("%A, %B ") + str(now.day) + now.strftime(", %Y")
    date_url = now.strftime("%Y-%m-%d")
    filed = now.strftime("%H:%M UTC")
    generated = now.strftime("Generated at %H:%M UTC")
    issue_no = db.get_issue_number(date_url)
    issue_label = f"No. {issue_no}<br/>" if issue_no is not None else ""

    domain = os.environ.get("DIGEST_DOMAIN", "")
    homepage = f"https://{domain}/issues/{date_url}" if domain else ""
    archive = os.environ.get("ARCHIVE_URL", "")
    author_name = os.environ.get("AUTHOR_NAME", "")
    author_url = os.environ.get("AUTHOR_URL", "")

    preheader = extract_preheader(selections)
    reading = calculate_reading_time(selections)
    counts = format_story_counts(selections)

    used: set[str] = set()

    def uniq(text: str) -> str:
        base = slugify(text) or "story"
        s, n = base, 2
        while s in used:
            s, n = f"{base}-{n}", n + 1
        used.add(s)
        return s

    body = []
    # top utility line
    body.append(
        _section(
            _txt(
                f'<a href="{homepage}" style="color:{MUTED};text-decoration:none;">View in browser</a> '
                f'<span style="color:{HAIR};">·</span> '
                f'<a href="{homepage}/translate" style="color:{MUTED};text-decoration:none;"><span>文A</span> Translate</a>',
                size=10,
                color=MUTED,
                font=MONO,
                align="center",
                extra='letter-spacing="1px" text-transform="uppercase"',
            ),
            padding=f"24px {SIDE} 0",
        )
        if homepage
        else ""
    )
    # masthead
    brand = _txt(
        f'Sean&#39;s Daily <span style="color:{ACCENT_INK};">Digest</span>',
        size=27,
        color=INK,
        weight="600",
        lh="1",
    )
    issue = _txt(
        f"{issue_label}Filed {filed}",
        size=10,
        color=MUTED,
        font=MONO,
        align="right",
        lh="1.55",
        extra=MONO_UP,
    )
    body.append(
        f'<mj-section padding="20px {SIDE} 0">'
        f'<mj-column width="60%" vertical-align="bottom">{brand}</mj-column>'
        f'<mj-column width="40%" vertical-align="bottom">{issue}</mj-column>'
        "</mj-section>"
    )
    body.append(
        _section(
            _txt(
                f'<span style="color:{ACCENT};">■</span> {date_str} &nbsp;/&nbsp; {reading} &nbsp;/&nbsp; {counts}',
                size=11,
                color=MUTED,
                font=MONO,
                padding="6px 0 12px",
                extra='letter-spacing="1px" text-transform="uppercase"',
            )
        )
    )
    body.append(
        f'<mj-section padding="0 {SIDE}"><mj-column><mj-divider border-width="2px" border-color="{INK}" padding="0" /></mj-column></mj-section>'
    )
    # AI notice
    body.append(
        _section(
            _txt(
                f'<span style="font-family:{MONO};color:{ACCENT_INK};font-weight:600;font-size:10px;letter-spacing:1px;">AI-WRITTEN</span>'
                f"&#160;&#160;Written by Claude, an assistant that can make mistakes - verify anything important against the linked sources. "
                f'Political leanings from <a href="{archive}/sources" style="color:{ACCENT_INK};">independent media assessors</a>.',
                size=12,
                font=SANS,
            ),
            padding=f"12px {SIDE}",
        )
    )
    # sections
    for label, num, key, is_brief in (
        ("Must Know", "01", "must_know", False),
        ("Should Know", "02", "should_know", True),
    ):
        items = selections.get(key, [])
        if not items:
            continue
        body.append(_section_header(num, label, gap=(key == "should_know")))
        for i, art in enumerate(items):
            slug = uniq(art.get("headline", ""))
            if i > 0:
                body.append(_separator(is_brief))
            body.append(_brief(art, slug, homepage, is_first=(i == 0)) if is_brief else _story(art, slug, homepage))
    # footer
    not_covered = (selections.get("not_covered_blurb") or "").strip()
    nav = f"{_link(archive, 'Past digests')} · {_link(f'{archive}/sources', 'Sources')}"
    if author_url:
        nav += f" · {_link(f'{author_url.rstrip("/")}/privacy', 'Privacy')}"
    nav += f" · {_link(unsubscribe_url, 'Unsubscribe')}"
    plug = f"Made by {_link(html.escape(author_url), html.escape(author_name))}" if author_name and author_url else ""
    footer_inner = (
        f'<mj-spacer height="48px" />'
        f'<mj-divider border-width="1px" border-color="{HAIR}" padding="0" />'
        f'<mj-spacer height="16px" />'
        f"{_txt(nav, size=12, color=MUTED, font=SANS, lh='1.7')}"
        f"{_txt('Reply to this email with feedback.', size=12, color=MUTED, font=SANS, padding='8px 0 0')}"
        + (
            _txt(f"Not covered today: {html.escape(not_covered)}", size=11, color=MUTED, font=SANS, padding="8px 0 0")
            if not_covered
            else ""
        )
        + (_txt(plug, size=11, color=MUTED, font=SANS, padding="8px 0 0") if plug else "")
        + _txt(generated, size=11, color=MUTED, font=SANS, padding="8px 0 0")
    )
    body.append(f'<mj-section padding="0 {SIDE}"><mj-column>{footer_inner}</mj-column></mj-section>')

    text_defaults = " ".join(f'{k}="{v}"' for k, v in _TXT_DEFAULTS.items() if k != "font-family")
    mjml = (
        "<mjml><mj-head>"
        '<mj-attributes><mj-all font-family="' + SERIF + '" />'
        f"<mj-text {text_defaults} /></mj-attributes>"
        "<mj-preview>" + html.escape(preheader) + "</mj-preview>"
        "</mj-head>"
        f'<mj-body background-color="{BG}" width="660px">' + "".join(body) + "</mj-body></mjml>"
    )
    result = mjml2html(mjml)
    # mjml-python usually returns a str; some versions return {html, errors}. Fail
    # LOUD on compile errors or empty output rather than let a broken/empty email
    # flow to send_broadcast and out to subscribers.
    if isinstance(result, dict):
        errors = result.get("errors")
        if errors:
            raise RuntimeError(f"MJML compile errors, refusing to send: {errors}")
        html_out = result.get("html", "")
    else:
        html_out = result
    if not html_out or not html_out.strip():
        raise RuntimeError("render_email produced empty HTML; refusing to send")
    return html_out
