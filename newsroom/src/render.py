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

    # Extract variables from :root (now guaranteed to be light mode)
    root_match = re.search(r":root\s*\{([^}]+)\}", css)
    if not root_match:
        return css

    # Parse variables
    variables = {}
    for match in re.finditer(r"--([a-z-]+)\s*:\s*([^;]+);", root_match.group(1)):
        variables[match.group(1)] = match.group(2).strip()

    # Replace var(--name) with values
    def replace_var(match):
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    css = re.sub(r"var\(--([a-z-]+)\)", replace_var, css)

    # Remove :root blocks - not supported in email
    css = re.sub(r":root\s*\{[^}]+\}", "", css)

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
        return html_content
    except Exception:
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
    html_content = inline_styles(html_content)

    return html_content


# =============================================================================
# Article Rendering
# =============================================================================


def generate_feedback_html(email: str) -> str:
    """Generate feedback buttons HTML with mailto links."""
    encoded = html.escape(email)
    return f"""<div class="feedback">
      <p>How was today's digest?</p>
      <div class="feedback-buttons">
        <a class="feedback-btn" href="mailto:{encoded}?subject=Feedback: Love it">🚀 Love it</a>
        <a class="feedback-btn" href="mailto:{encoded}?subject=Feedback: Good">😊 Good</a>
        <a class="feedback-btn" href="mailto:{encoded}?subject=Feedback: So so">😐 So so</a>
      </div>
    </div>"""


def render_article(article: dict, slug: str, include_reporting_varies: bool = True) -> str:
    """Render a single article (must_know or should_know) to HTML."""
    headline = html.escape(article.get("headline", ""))
    summary = html.escape(article.get("summary", ""))
    why = html.escape(article.get("why_it_matters", ""))

    # Sources line -- group by (name, bias) to avoid repetition
    groups: dict[tuple[str, str], list[str]] = {}
    for src in article.get("sources", []):
        name = html.escape(src.get("name", ""))
        bias = html.escape(src.get("bias", ""))
        url = src.get("url", "")
        if not name:
            continue
        key = (name, bias)
        groups.setdefault(key, [])
        if url and is_safe_url(url) and _has_article_path(url):
            groups[key].append(html.escape(url))

    sources_parts = []
    for (name, bias), urls in groups.items():
        if len(urls) == 1:
            sources_parts.append(f'<a href="{urls[0]}">{name}</a> ({bias})')
        elif len(urls) > 1:
            numbered = ", ".join(f'<a href="{u}">{i}</a>' for i, u in enumerate(urls, 1))
            sources_parts.append(f"{name} ({bias}) [{numbered}]")
        else:
            sources_parts.append(f"{name} ({bias})")
    sources_line = " · ".join(sources_parts)

    # Build article HTML
    parts = [
        f'    <article id="{slug}">',
        f'      <h3><a href="#{slug}" class="anchor" aria-label="Link to this story">{ANCHOR_SVG}</a>{headline}</h3>',
        f"      <p>{summary}</p>",
        f'      <p class="why"><strong>Why it matters:</strong> {why}</p>',
    ]

    # Optional: reporting_varies (only for must_know)
    if include_reporting_varies:
        reporting_varies = article.get("reporting_varies", [])
        if reporting_varies:
            parts.append('      <div class="reporting-varies">')
            parts.append("        <strong>How reporting varies:</strong>")
            parts.append("        <ul>")
            for rv in reporting_varies:
                src = html.escape(rv.get("source", ""))
                bias = html.escape(rv.get("bias", ""))
                angle = html.escape(rv.get("angle", ""))
                parts.append(f"          <li><em>{src}</em> ({bias}): {angle}</li>")
            parts.append("        </ul>")
            parts.append("      </div>")

    parts.append(f'      <p class="sources">{sources_line}</p>')
    parts.append("    </article>")

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

    # Render must_know
    must_know_html = "\n".join(
        render_article(article, slug=unique_slug(article.get("headline", "")), include_reporting_varies=True)
        for article in selections.get("must_know", [])
    )

    # Render should_know
    should_know_html = "\n".join(
        render_article(article, slug=unique_slug(article.get("headline", "")), include_reporting_varies=False)
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
    """Format story counts per section for meta line."""
    must = len(selections.get("must_know", []))
    should = len(selections.get("should_know", []))
    return f"{must} 🥇 · {should} 🥈"


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
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    digest_domain = os.environ.get("DIGEST_DOMAIN", "")
    source_url = os.environ.get("SOURCE_URL", "")
    archive_url = os.environ.get("ARCHIVE_URL", "")

    # Load CSS: minify but keep variables for dark mode support in browser
    if not styles_file.exists():
        raise RuntimeError(f"Styles file not found: {styles_file}")
    css = styles_file.read_text()
    styles = minify_css(css)  # Keep CSS variables intact

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
    content = content.replace("{{READING_TIME}}", calculate_reading_time(selections))
    content = content.replace("{{STORY_COUNT}}", story_count_str)
    content = content.replace("{{GENERATED_AT}}", generated_at)
    content = content.replace("{{PREHEADER}}", html.escape(preheader))

    # Optional: replace SOURCE_URL if configured, otherwise remove the links
    if source_url:
        content = content.replace("{{SOURCE_URL}}", source_url)
    else:
        # Remove the open source sentence in AI notice if not configured
        content = re.sub(
            r'\s*This project is <a href="\{\{SOURCE_URL\}\}">[^<]+</a> and contributions are welcome\.', "", content
        )

    # Feedback buttons (mailto links with pre-filled subject)
    feedback_email = os.environ.get("RESEND_FROM", "")
    if feedback_email:
        content = content.replace("{{FEEDBACK_BUTTONS}}", generate_feedback_html(feedback_email))
    else:
        content = re.sub(r"\s*\{\{FEEDBACK_BUTTONS\}\}", "", content)

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

    # Replace HOMEPAGE_URL and SUBSCRIBE_URL for header links
    if digest_domain:
        homepage_url = f"https://{digest_domain}/{date_url}"
        subscribe_url = f"https://{digest_domain}/#subscribe"
        content = content.replace("{{HOMEPAGE_URL}}", homepage_url)
        content = content.replace("{{SUBSCRIBE_URL}}", subscribe_url)
    else:
        content = re.sub(r'\s*<nav class="header-links">.*?</nav>', "", content, flags=re.DOTALL)

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
        content = re.sub(r' · <a href="\{\{PRIVACY_URL\}\}">Privacy</a>', "", content)

    digest_path.write_text(content)
    logger.info("Date: %s", date_str)
