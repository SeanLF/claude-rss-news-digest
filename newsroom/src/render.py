"""HTML rendering for digest output.

Handles text utilities, CSS processing, article rendering,
and placeholder replacement.
"""

import csv
import html
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import config
import db

logger = logging.getLogger(__name__)

# Set CSV field size limit to prevent memory issues with malformed feeds
csv.field_size_limit(1_000_000)  # 1MB max


# =============================================================================
# Text Utilities
# =============================================================================


ANCHOR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="'
    "M7.775 3.275a.75.75 0 001.06 1.06l1.25-1.25a2 2 0 112.83 2.83l-2.5 2.5a2 2 0 01-2.83 0"
    " .75.75 0 00-1.06 1.06 3.5 3.5 0 004.95 0l2.5-2.5a3.5 3.5 0 00-4.95-4.95l-1.25 1.25z"
    "m-.025 9.45a.75.75 0 01-1.06-1.06l-1.25 1.25a2 2 0 01-2.83-2.83l2.5-2.5a2 2 0 012.83 0"
    " .75.75 0 001.06-1.06 3.5 3.5 0 00-4.95 0l-2.5 2.5a3.5 3.5 0 004.95 4.95l1.25-1.25z"
    '"/></svg>'
)


def slugify(text: str, max_length: int = 60) -> str:
    """Convert headline text to a URL-safe slug for anchor IDs."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")[:max_length].rstrip("-")
    return slug or "story"


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)  # Remove tags
    text = html.unescape(text)  # Decode &amp; etc
    text = re.sub(r"\s+", " ", text).strip()  # Normalize whitespace
    return text


def is_safe_url(url: str) -> bool:
    """Validate URL has a safe scheme (http/https only)."""
    return url.startswith(("http://", "https://"))


def _has_article_path(url: str) -> bool:
    """Check if URL has a meaningful path beyond bare domain.

    Bare domain URLs (e.g. https://www.washingtonpost.com or
    https://www.washingtonpost.com/) link to homepages -- useless
    for readers expecting a specific article.
    """
    path = urlparse(url).path
    return path not in ("", "/")


def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars/token for CSV with URLs)."""
    return len(text) // 4


def calculate_reading_time(selections: dict, words_per_minute: int = 200) -> str:
    """Calculate reading time from selections JSON. Returns string like '13 min read'.

    Counts only editorial content readers actually read: headlines, summaries,
    why_it_matters, and reporting angles. Excludes source citations, nav text,
    CSS, and boilerplate that inflate HTML-based estimates.
    """
    parts: list[str] = []
    for tier in ("must_know", "should_know"):
        for article in selections.get(tier, []):
            parts.append(article.get("headline", ""))
            parts.append(article.get("summary", ""))
            parts.append(article.get("why_it_matters", ""))
            for rv in article.get("reporting_varies", []):
                parts.append(rv.get("angle", ""))
    word_count = len(" ".join(parts).split())
    minutes = max(1, round(word_count / words_per_minute))
    return f"{minutes} min read"


# =============================================================================
# CSS Processing
# =============================================================================


def minify_css(css: str) -> str:
    """Minify CSS by removing comments, whitespace, and newlines."""
    # Remove comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Remove whitespace around special characters
    css = re.sub(r"\s*([{};:,>])\s*", r"\1", css)
    # Collapse multiple whitespace
    css = re.sub(r"\s+", " ", css)
    return css.strip()


def _strip_media_block(css: str, keyword: str) -> str:
    """Remove @media blocks containing keyword using brace-counting (handles nesting)."""
    result = []
    i = 0
    while i < len(css):
        match = re.search(rf"@media\s*\([^)]*{re.escape(keyword)}[^)]*\)\s*\{{", css[i:])
        if not match:
            result.append(css[i:])
            break
        result.append(css[i : i + match.start()])
        # Walk forward counting braces to find matching close
        j = i + match.end()
        depth = 1
        while j < len(css) and depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(result)


def resolve_css_variables(css: str) -> str:
    """Replace CSS variables with their values (light mode only for email).

    Email clients don't support CSS variables or prefers-color-scheme, so we
    resolve to light mode values and strip the dark mode media query.
    """
    # Strip dark mode media query first so :root extraction always gets light values
    css = _strip_media_block(css, "prefers-color-scheme")

    # Extract variables from EVERY light-mode `:root {…}` block. tokens.css splits
    # the token set across three plain :root blocks (shared core / chrome / digest
    # -- the digest's --hair lives in the third), so reading only the first would
    # leave var(--hair) unresolved in email. The `:root\s*\{` pattern matches the
    # plain blocks but NOT `:root[data-theme=...]{…}` (a `[` sits between :root and
    # the brace), so the explicit-toggle blocks are intentionally excluded.
    root_blocks = re.findall(r":root\s*\{([^}]+)\}", css)
    if not root_blocks:
        return css

    # Parse variables (later blocks win, but the three blocks define disjoint
    # names). The name class MUST include digits: the design tokens use --ink2,
    # so an [a-z-]+ class would silently fail to resolve var(--ink2) for email.
    variables = {}
    for block in root_blocks:
        for match in re.finditer(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block):
            variables[match.group(1)] = match.group(2).strip()

    # Replace var(--name) with values
    def replace_var(match):
        var_name = match.group(1)
        if var_name not in variables:
            # An unresolved var() ships verbatim and renders as broken CSS in every
            # inbox -- surface it rather than silently degrading the whole list.
            logger.warning("unresolved var(--%s) shipping in email CSS", var_name)
            return match.group(0)
        return variables[var_name]

    css = re.sub(r"var\(--([a-z0-9-]+)\)", replace_var, css)

    # Remove :root blocks - not supported in email, and dead weight once resolved.
    # Also drop the explicit-toggle :root[data-theme=...]{…} blocks (tokens.css:74+):
    # they ship the dark/light palette as inert declarations no email element can
    # match (the email <html> has no data-theme), so strip them rather than bloat
    # every send. The optional [..] attribute is what the plain pattern above misses.
    # NB: `[^}]*` assumes each :root block is brace-balanced -- a stray `{` typo in
    # tokens.css would let this consume the next rule. tokens.css is the trusted,
    # tested source, so that's acceptable; don't point this at untrusted CSS.
    css = re.sub(r":root(?:\[[^\]]*\])?\s*\{[^}]*\}", "", css)

    return css


def inline_styles(html_content: str) -> str:
    """Inline CSS styles for email compatibility using premailer."""
    try:
        from premailer import transform

        return transform(
            html_content,
            remove_classes=False,
            keep_style_tags=True,  # Keep for clients that support <style>
            strip_important=False,
            cssutils_logging_level=50,  # Suppress warnings
        )
    except ImportError:
        logger.warning("premailer not available; sending email with un-inlined styles")
        return html_content
    except Exception as exc:
        # Un-inlined HTML degrades in Word-engine clients (Outlook) for the whole list;
        # error-level so a systematic premailer break is Sentry-visible, not swallowed.
        logger.error("premailer failed to inline styles (%s); sending email un-inlined", exc)
        return html_content


def prepare_for_email(html_content: str) -> str:
    """Prepare HTML for email delivery.

    Resolves CSS variables to light mode values and inlines styles.
    Email clients don't support CSS variables or prefers-color-scheme.
    """

    def resolve_style_block(match):
        css = match.group(1)
        resolved_css = resolve_css_variables(css)
        minified_css = minify_css(resolved_css)
        return f"<style>{minified_css}</style>"

    html_content = re.sub(r"<style>(.*?)</style>", resolve_style_block, html_content, flags=re.DOTALL)
    # Email-only: declare light-only. The CSS was resolved to light hex, so a
    # client force-inverting for dark mode would mangle the accent/bias colours
    # unpredictably; these metas ask Apple Mail/Gmail to keep it light. Injected
    # here (not in the template) so the WEB view keeps its own light/dark toggle.
    with_meta = html_content.replace(
        "<head>",
        '<head>\n  <meta name="color-scheme" content="light">\n  <meta name="supported-color-schemes" content="light">',
        1,
    )
    if with_meta == html_content:
        # Fail loud: a template refactor that drops the bare `<head>` literal would
        # silently strip these metas, re-exposing the light-only palette to dark-mode
        # auto-inversion in every send -- invisible degradation on a shipped artifact.
        logger.error("prepare_for_email: no <head> found; color-scheme metas NOT injected")
    html_content = with_meta
    # Physically drop web-only blocks. Gmail unwraps <details>/<summary> and promotes
    # their children, discarding the display:none that hides the web-only source table
    # -- so it leaks into the email. Removing the block outright is the only reliable
    # fix; the email keeps its own .email-only static source line.
    html_content = re.sub(r"<details\b[^>]*\bweb-only\b[^>]*>.*?</details>", "", html_content, flags=re.DOTALL)
    html_content = inline_styles(html_content)

    return html_content


# =============================================================================
# Article Rendering
# =============================================================================


def _bias_bucket(bias: str) -> str:
    """Map a source's political-leaning label to a bias bucket: l / c / r.

    lean-left/left/far-left -> l, lean-right/right/far-right -> r, everything
    else (center, lean-center, unknown, blank) -> c.
    """
    b = bias.strip().lower()
    if b in ("lean-left", "left", "far-left"):
        return "l"
    if b in ("lean-right", "right", "far-right"):
        return "r"
    # Everything else buckets center, but surface a genuinely unrecognized label so a
    # sources.json typo can't silently skew the bias bar toward center unnoticed.
    if b and b not in _KNOWN_CENTER:
        logger.warning("unmapped bias label %r bucketed as center", bias)
    return "c"


_BUCKET_ORDER = ("l", "c", "r")
_BUCKET_WORD = {"l": "left", "c": "center", "r": "right"}
# Labels that legitimately bucket center (blank = known missing data, not a warn).
_KNOWN_CENTER = frozenset(
    {
        "",
        "center",
        "centre",
        "lean-center",
        "lean-centre",
        "center-left",
        "center-right",
        "centre-left",
        "centre-right",
        "central",
        "mixed",
    }
)


def _collect_outlets(article: dict) -> list[dict]:
    """Group an article's sources by outlet, preserving input order.

    Each returned dict is {name, bias, bucket, urls}. Only URLs that pass the
    safe-scheme + real-article-path guards are kept; multiple articles from the
    same outlet collapse into one entry with an ordered url list. Outlets left
    with zero usable URLs are dropped (nothing for the reader to open).
    """
    order: list[dict] = []
    index: dict[str, dict] = {}
    for src in article.get("sources", []):
        name = src.get("name", "")
        if not name:
            continue
        entry = index.get(name)
        if entry is None:
            entry = {"name": name, "bias": src.get("bias", ""), "bucket": _bias_bucket(src.get("bias", "")), "urls": []}
            index[name] = entry
            order.append(entry)
        url = src.get("url", "")
        if url and is_safe_url(url) and _has_article_path(url):
            entry["urls"].append(url)
    result = [o for o in order if o["urls"]]
    # A story that had sources but loses them all to the URL guards ships with no bias
    # bar, no count, no "view online" link -- total attribution loss. Surface it.
    if order and not result:
        logger.warning(
            "all %d source outlet(s) dropped (no article-path URLs); story ships with no source block",
            len(order),
        )
    return result


def _render_sources_block(outlets: list[dict], slug: str) -> str:
    """Render the shared bias glyph as a web-only <details> source table plus an
    email-only static line ("N sources · a left · … · view all N online").

    Bias-bar segments and the spread label emit ONLY non-empty buckets, in
    l/c/r order. The "source(s)" word is singularized; bucket counts are plain.
    Table rows are ordered left->center->right (input order within a bucket),
    one row per outlet with numbered article links. All text is html-escaped.
    """
    if not outlets:
        return ""

    counts = {"l": 0, "c": 0, "r": 0}
    for o in outlets:
        counts[o["bucket"]] += 1
    total = len(outlets)

    segs = "".join(f'<span class="seg {b}" style="flex:{counts[b]}"></span>' for b in _BUCKET_ORDER if counts[b])
    src_word = "source" if total == 1 else "sources"
    buckets_label = " · ".join(f"{counts[b]} {_BUCKET_WORD[b]}" for b in _BUCKET_ORDER if counts[b])
    spread_label = f'<span class="n">{total} {src_word}</span> · {buckets_label}'
    biasbar = f'<span class="biasbar" aria-hidden="true">{segs}</span>'

    ordered = [o for b in _BUCKET_ORDER for o in outlets if o["bucket"] == b]
    rows = ""
    for o in ordered:
        links = " ".join(f'<a href="{html.escape(u)}">{i}</a>' for i, u in enumerate(o["urls"], 1))
        rows += (
            f'<tr><td class="nm">{html.escape(o["name"])}</td>'
            f'<td class="ln">{html.escape(o["bias"])}</td>'
            f'<td class="ar">{links}</td></tr>'
        )

    details = (
        f'<details class="srcbox web-only"><summary class="spread">{biasbar}'
        f'<span class="spread-label">{spread_label}</span></summary>'
        '<table class="src-table"><thead><tr>'
        '<th scope="col">Outlet</th><th scope="col">Leaning</th><th scope="col">Articles</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></details>"
    )
    # Email biasbar: a presentation table, NOT flex. Gmail/Outlook drop
    # display:flex, which collapsed the segments and knocked the bar and text
    # out of alignment. Proportional cells carry the l/c/r colours by class
    # (inlined to light-mode hex in prepare_for_email); aria-hidden decoration.
    bar_cells = "".join(
        f'<td class="seg {b}" width="{round(100 * counts[b] / total)}%" height="4"></td>'
        for b in _BUCKET_ORDER
        if counts[b]
    )
    email_bar = (
        f'<table role="presentation" class="biasbar-e" width="120" cellpadding="0" '
        f'cellspacing="0" aria-hidden="true"><tr>{bar_cells}</tr></table>'
    )
    # HOMEPAGE_URL is this issue's dated page (https://domain/DATE); the story
    # anchor #slug lives there. (ARCHIVE_URL is the undated archive index -- linking
    # there dropped the date and the anchor resolved against the wrong page.)
    # {{HOMEPAGE_URL}} is filled (or emptied) by replace_placeholders.
    # Bar STACKED above the meta line: an inline bar+text row misaligned
    # vertically and cramped/wrapped on narrow mobile widths. Stacking sidesteps
    # both -- the bar is a short rule, the meta text flows full-width beneath it.
    email_line = (
        '<div class="srcline email-only">'
        f"{email_bar}"
        f'<div class="sl-txt"><span class="spread-label">{spread_label}</span>'
        f' · <a href="{{{{HOMEPAGE_URL}}}}#{slug}">view all {total} {src_word} online</a></div>'
        "</div>"
    )
    return details + email_line


def render_article(
    article: dict,
    slug: str,
    *,
    is_brief: bool = False,
    is_first: bool = False,
) -> str:
    """Render a single story (must_know) or brief (should_know) to HTML.

    Stories emit the .head/.lede/.why/.varies markup; briefs emit the compact
    .brief h3/.summary markup with no why/varies. Both carry the shared sources
    block. The first must-know story gets class="first" (no top rule).
    """
    raw_headline = article.get("headline", "")
    headline = html.escape(raw_headline)
    summary = html.escape(article.get("summary", ""))
    why = html.escape(article.get("why_it_matters", ""))
    anchor = f'<a class="anchor" href="#{slug}" aria-label="Copy link: {html.escape(raw_headline[:50])}"></a>'

    # A continuing thread shows an "Ongoing · day N" eyebrow, and -- when there's
    # a delta -- its lede/summary is REPLACED with what's new today (top verified
    # facts) so a returning reader sees the development, not a generic re-describe.
    # Falls back to the WRITE summary on a quiet day (no delta).
    thread = article.get("thread") or {}
    eyebrow = ""
    if thread.get("day", 0) >= 2:
        eyebrow = f'<p class="eyebrow"><span class="loc">Ongoing</span> · day {thread["day"]}</p>'
    delta = (thread.get("delta") or "").strip()
    body = html.escape(delta) if delta else summary

    sources_block = _render_sources_block(_collect_outlets(article), slug)

    if is_brief:
        parts = [f'<article class="brief" id="{slug}">', f"<h3>{headline}{anchor}</h3>"]
        if eyebrow:
            parts.append(eyebrow)
        parts.append(f'<p class="summary">{body}</p>')
        parts.append(sources_block)
        parts.append("</article>")
        return "\n".join(parts)

    cls = ' class="first"' if is_first else ""
    parts = [f'<article{cls} id="{slug}">', f'<h3 class="head">{headline}{anchor}</h3>']
    if eyebrow:
        parts.append(eyebrow)
    parts.append(f'<p class="lede">{body}</p>')
    # COHERENCE can strip why_it_matters to "" (field-aware graceful degradation
    # in merge.py) when only that field failed fact-checking; an empty why must
    # not leave a dangling label with no content.
    if why.strip():
        parts.append(f'<div class="why"><span class="lbl">Why it matters</span><p>{why}</p></div>')
    # reporting_varies: must-know only, one <p> per angle.
    reporting_varies = article.get("reporting_varies", [])
    if reporting_varies:
        rv_parts = [
            f"<p><b>{html.escape(rv.get('source', ''))}:</b> {html.escape(rv.get('angle', ''))}</p>"
            for rv in reporting_varies
        ]
        parts.append(f'<div class="varies"><span class="lbl">How reporting varies</span>{"".join(rv_parts)}</div>')
    parts.append(sources_block)
    parts.append("</article>")
    return "\n".join(parts)


def render_digest(selections: dict, template_file: Path) -> str:
    """Render selections.json to complete HTML string."""
    if not template_file.exists():
        raise RuntimeError(f"Template file not found: {template_file}")
    template = template_file.read_text()

    # Track slugs for dedup across sections
    used_slugs: set[str] = set()

    def unique_slug(headline: str) -> str:
        slug = slugify(headline)
        if slug not in used_slugs:
            used_slugs.add(slug)
            return slug
        n = 2
        while f"{slug}-{n}" in used_slugs:
            n += 1
        deduped = f"{slug}-{n}"
        used_slugs.add(deduped)
        return deduped

    # Render must_know (stories). First one gets class="first" (no top rule).
    must_know_html = "\n".join(
        render_article(
            article,
            slug=unique_slug(article.get("headline", "")),
            is_first=(i == 0),
        )
        for i, article in enumerate(selections.get("must_know", []))
    )

    # Render should_know (briefs)
    should_know_html = "\n".join(
        render_article(
            article,
            slug=unique_slug(article.get("headline", "")),
            is_brief=True,
        )
        for article in selections.get("should_know", [])
    )

    # Fill template
    result = template
    result = result.replace("{{MUST_KNOW}}", must_know_html)
    result = result.replace("{{SHOULD_KNOW}}", should_know_html)

    return result


def extract_headlines(selections: dict) -> list[dict]:
    """Extract all headlines from selections for deduplication tracking.

    One row per source per story. source_id and original_title are read
    directly from source dicts (injected by resolve_article_ids).
    """
    headlines = []

    # Top-tier articles (must_know, should_know) -- one row per source
    for tier in ["must_know", "should_know"]:
        for item in selections.get(tier, []):
            editorial_headline = item.get("headline", "")
            cluster_id = item.get("cluster_id")
            for src in item.get("sources", []):
                headlines.append(
                    {
                        "headline": editorial_headline,
                        "tier": tier,
                        "source_id": src.get("source_id"),
                        "original_title": src.get("original_title"),
                        "cluster_id": cluster_id,
                    }
                )

    return headlines


def extract_preheader(selections: dict) -> str:
    """Extract preheader text from selections for email preview."""
    return selections.get("preheader", "")


def format_story_counts(selections: dict) -> str:
    """Format story counts for the dateline meta ("5 must-know · 8 should-know")."""
    must = len(selections.get("must_know", []))
    should = len(selections.get("should_know", []))
    return f"{must} must-know · {should} should-know"


def _strip_nav_link(content: str, url_placeholder: str, text: str) -> str:
    """Remove an optional footer-nav ``<a>`` (whose href is an unconfigured
    ``{{url_placeholder}}``) plus one adjacent " · " separator, whatever side it
    sits on -- so no orphan middot or leftover placeholder survives regardless of
    which sibling links were already stripped. Falls back to removing the bare
    link when it is the nav's only remaining child.
    """
    link = r'<a href="\{\{' + re.escape(url_placeholder) + r'\}\}">' + re.escape(text) + r"</a>"
    content = re.sub(link + r"\s*·\s*", "", content)  # link first: consume trailing sep
    content = re.sub(r"\s*·\s*" + link, "", content)  # link later: consume leading sep
    content = re.sub(link, "", content)  # bare link
    return content


def replace_placeholders(
    digest_path: Path,
    selections: dict,
    styles_file: Path,
    preheader: str = "",
):
    """Replace all placeholders in digest HTML (styles, name, date, etc).

    CSS variables are preserved to support dark mode when viewing in browser.
    Email preparation (resolving variables, inlining) happens in send_broadcast().
    """
    now = datetime.now(UTC)
    date_str = now.strftime("%A, %B ") + str(now.day) + now.strftime(", %Y")
    date_url = now.strftime("%Y-%m-%d")
    generated_at = now.strftime("Generated at %H:%M UTC")
    filed_time = now.strftime("%H:%M UTC")
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    digest_domain = os.environ.get("DIGEST_DOMAIN", "")
    source_url = os.environ.get("SOURCE_URL", "")
    archive_url = os.environ.get("ARCHIVE_URL", "")

    # Edition number ("No. N") from the digest count. None when no DB is wired
    # -- render then drops the whole "No." line rather than shipping a stray
    # "No. —". Production (and now --test-send) wire the DB, so a real number shows.
    issue_no = db.get_issue_number(date_url)
    issue_label = f"No. {issue_no}<br>" if issue_no is not None else ""

    # Load CSS: tokens.css (the :root token source) PREPENDED to digest.css so
    # the component rules' var(--…) resolve. Kept un-inlined here (variables +
    # dark-mode media query intact) for the browser; email inlining happens later
    # in prepare_for_email(). tokens.css is optional so unit tests that pass only
    # a bare styles_file still render (with a warning), but production ships it.
    if not styles_file.exists():
        raise RuntimeError(f"Styles file not found: {styles_file}")
    css_parts = []
    if config.TOKENS_FILE.exists():
        css_parts.append(config.TOKENS_FILE.read_text())
    else:
        logger.warning("Tokens file not found: %s -- CSS variables will not resolve", config.TOKENS_FILE)
    css_parts.append(styles_file.read_text())
    styles = minify_css("\n".join(css_parts))  # Keep CSS variables intact

    content = digest_path.read_text()

    # Verify required placeholders exist before replacing
    for placeholder in ["{{DIGEST_NAME}}", "{{DATE}}", "{{STYLES}}"]:
        if placeholder not in content:
            raise RuntimeError(f"Missing placeholder {placeholder} in digest")

    # Story counts per section for meta line
    story_count_str = format_story_counts(selections)

    content = content.replace("{{STYLES}}", styles)
    content = content.replace("{{DIGEST_NAME}}", html.escape(digest_name))
    content = content.replace("{{DATE}}", date_str)
    content = content.replace("{{DATE_ISO}}", date_url)
    content = content.replace("{{ISSUE_LABEL}}", issue_label)
    content = content.replace("{{FILED_TIME}}", filed_time)
    content = content.replace("{{READING_TIME}}", calculate_reading_time(selections))
    content = content.replace("{{STORY_COUNT}}", story_count_str)
    content = content.replace("{{GENERATED_AT}}", generated_at)
    content = content.replace("{{PREHEADER}}", html.escape(preheader))

    # Optional: not-covered footer garnish (what SELECT deliberately filtered,
    # copied through by merge.assemble_selections). Strip the whole line when
    # absent, same pattern as {{AUTHOR_PLUG}} below.
    not_covered = selections.get("not_covered_blurb")
    if isinstance(not_covered, str) and not_covered.strip():
        content = content.replace("{{NOT_COVERED}}", f"Not covered today: {html.escape(not_covered.strip())}")
    else:
        if not_covered is not None:
            # Present but unusable (wrong type, or blank string) -- matters most
            # in --write-only re-renders where render.py is running against a
            # possibly stale/hand-edited selections.json with no merge.py pass
            # in between to have already logged the problem.
            logger.warning(
                "not_covered_blurb present but unusable (type=%s) -- omitting footer line",
                type(not_covered).__name__,
            )
        content = re.sub(r'\s*<p class="not-covered">\{\{NOT_COVERED\}\}</p>', "", content)

    # Optional: replace SOURCE_URL if configured, otherwise remove the links
    if source_url:
        content = content.replace("{{SOURCE_URL}}", source_url)
    else:
        # Remove the open source sentence in AI notice if not configured
        content = re.sub(
            r'\s*This project is <a href="\{\{SOURCE_URL\}\}">[^<]+</a> and contributions are welcome\.', "", content
        )

    # Optional: author plug
    author_name = os.environ.get("AUTHOR_NAME", "")
    author_url = os.environ.get("AUTHOR_URL", "")
    if author_name and author_url and is_safe_url(author_url):
        author_plug = f'Made by <a href="{html.escape(author_url)}">{html.escape(author_name)}</a>'
        content = content.replace("{{AUTHOR_PLUG}}", author_plug)
    elif author_name:
        content = content.replace("{{AUTHOR_PLUG}}", f"Made by {html.escape(author_name)}")
    else:
        content = re.sub(r'\s*<p class="footer-meta">\{\{AUTHOR_PLUG\}\}</p>', "", content)

    # Replace HOMEPAGE_URL (footer translate line) and SUBSCRIBE_URL (footer nav)
    if digest_domain:
        homepage_url = f"https://{digest_domain}/{date_url}"
        subscribe_url = f"https://{digest_domain}/#subscribe"
        content = content.replace("{{HOMEPAGE_URL}}", homepage_url)
        content = content.replace("{{SUBSCRIBE_URL}}", subscribe_url)
    else:
        # No domain -> no route for the Subscribe link, the top view-in-browser /
        # translate line (both HOMEPAGE_URL), or the footer reply prompt; drop them
        # whole rather than ship a dangling placeholder. These email-only surfaces
        # only matter for a real send, which always sets the domain (prod).
        content = _strip_nav_link(content, "SUBSCRIBE_URL", "Subscribe")
        content = re.sub(r'\s*<p class="footer-actions email-only">.*?</p>', "", content, flags=re.DOTALL)
        content = re.sub(r'\s*<p class="webview email-only">.*?</p>', "", content, flags=re.DOTALL)
        # Blank any HOMEPAGE_URL still in body (the per-story "view sources online"
        # links) so they degrade to a bare #anchor rather than trip the residual sweep.
        content = content.replace("{{HOMEPAGE_URL}}", "")

    # Optional: archive URL for "Past digests" link
    if archive_url and is_safe_url(archive_url):
        content = content.replace("{{ARCHIVE_URL}}", html.escape(archive_url))
    else:
        content = re.sub(r'<a href="\{\{ARCHIVE_URL\}\}[^"]*">[^<]+</a> · ', "", content)
        content = re.sub(r'<a href="\{\{ARCHIVE_URL\}\}/sources">([^<]+)</a>', r"\1", content)
        content = content.replace("{{ARCHIVE_URL}}", "")

    # Privacy policy URL (derived from author homepage)
    if author_url and is_safe_url(author_url):
        privacy_url = f"{author_url.rstrip('/')}/privacy"
        content = content.replace("{{PRIVACY_URL}}", html.escape(privacy_url))
    else:
        content = _strip_nav_link(content, "PRIVACY_URL", "Privacy")

    # Residual-placeholder sweep: after every fill, any leftover {{…}} would ship
    # blank to real subscribers. Exclude the <style> block (minified CSS's braces
    # are not placeholders) and Resend's triple-brace {{{RESEND_UNSUBSCRIBE_URL}}}
    # (filled per-recipient at send time). Any remaining {{…}} is a bug -- fail
    # loud rather than send a broken digest.
    scan = re.sub(r"<style>.*?</style>", "", content, flags=re.DOTALL)
    # Drop Resend's triple-brace merge tags generically (any {{{NAME}}}) so adding a
    # second one later can't false-crash a good send.
    scan = re.sub(r"\{\{\{[^{}]+\}\}\}", "", scan)
    # Only UPPER_SNAKE tokens are real placeholders. html.escape() does NOT escape { },
    # so editorial prose can legitimately carry braces (a story quoting `${{ secrets.X }}`
    # or Vue/Handlebars `{{ user.name }}`); matching those would abort the entire send and
    # ship nothing. Restrict the sweep to the actual placeholder convention.
    leftover = re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", scan)
    if leftover:
        raise RuntimeError(f"Unfilled placeholder in rendered digest: {leftover.group(0)}")

    digest_path.write_text(content)
    logger.info("Date: %s", date_str)
