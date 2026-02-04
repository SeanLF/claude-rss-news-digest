"""HTML rendering for digest output.

Handles text utilities, CSS processing, article rendering,
and placeholder replacement.
"""

import csv
import html
import os
import re
from datetime import UTC, datetime
from pathlib import Path

# Region display configuration: (display_name, emoji)
REGION_CONFIG = {
    "europe": ("Europe", "🌍"),
    "americas": ("Americas", "🌎"),
    "asia_pacific": ("Asia-Pacific", "🌏"),
    "middle_east_africa": ("Middle East & Africa", "🌍"),
    "tech": ("Tech", "🤖"),
}

# Region display order (Americas first - where subscribers are)
REGION_ORDER = ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"]

# Set CSV field size limit to prevent memory issues with malformed feeds
csv.field_size_limit(1_000_000)  # 1MB max


# =============================================================================
# Text Utilities
# =============================================================================


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)  # Remove tags
    text = html.unescape(text)  # Decode &amp; etc
    text = re.sub(r"\s+", " ", text).strip()  # Normalize whitespace
    return text


def is_safe_url(url: str) -> bool:
    """Validate URL has a safe scheme (http/https only)."""
    return url.startswith(("http://", "https://"))


def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars/token for CSV with URLs)."""
    return len(text) // 4


def calculate_reading_time(html_content: str, words_per_minute: int = 200) -> str:
    """Calculate reading time from HTML content. Returns string like '5 min read'."""
    plain_text = strip_html(html_content)
    word_count = len(plain_text.split())
    minutes = max(1, round(word_count / words_per_minute))
    return f"{minutes} min read"


def markdown_to_html(text: str) -> str:
    """Convert markdown links [text](url) to HTML <a> tags."""

    def replace_link(match):
        link_text = html.escape(match.group(1))
        url = match.group(2)
        if is_safe_url(url):
            return f'<a href="{html.escape(url)}">{link_text}</a>'
        return link_text  # Return just text if URL is unsafe

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)


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


def resolve_css_variables(css: str) -> str:
    """Replace CSS variables with their values (light mode only for email).

    Email clients don't support CSS variables or prefers-color-scheme, so we
    resolve to light mode values and strip the dark mode media query.
    """
    # Extract variables from :root (first occurrence = light mode)
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

    # Remove :root blocks and @media (prefers-color-scheme) - not supported in email
    css = re.sub(r":root\s*\{[^}]+\}", "", css)
    css = re.sub(r"@media\s*\([^)]*prefers-color-scheme[^)]*\)\s*\{[^}]*\{[^}]*\}[^}]*\}", "", css)

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

    html_content = re.sub(r"<style>([^<]+)</style>", resolve_style_block, html_content)
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


def render_article(article: dict, include_reporting_varies: bool = True) -> str:
    """Render a single article (must_know or should_know) to HTML."""
    headline = html.escape(article.get("headline", ""))
    summary = html.escape(article.get("summary", ""))
    why = html.escape(article.get("why_it_matters", ""))

    # Sources line
    sources_html = []
    for src in article.get("sources", []):
        name = html.escape(src.get("name", ""))
        url = src.get("url", "")
        bias = html.escape(src.get("bias", ""))
        if name and url and is_safe_url(url):
            sources_html.append(f'<a href="{html.escape(url)}">{name}</a> ({bias})')
    sources_line = " · ".join(sources_html)

    # Build article HTML
    parts = [
        "    <article>",
        f"      <h3>{headline}</h3>",
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


def render_signal(item: dict) -> str:
    """Render a quick signal or below_fold item to HTML."""
    headline = html.escape(item.get("headline", ""))
    src = item.get("source", {})
    name = html.escape(src.get("name", ""))
    url = src.get("url", "")
    if url and is_safe_url(url):
        return f'      <p class="signal">{headline} — <a href="{html.escape(url)}">{name}</a></p>'
    return f'      <p class="signal">{headline} — {name}</p>'


def render_digest(selections: dict, template_file: Path) -> str:
    """Render selections.json to complete HTML string."""
    if not template_file.exists():
        raise RuntimeError(f"Template file not found: {template_file}")
    template = template_file.read_text()

    # Render regional summary
    regional_summary = selections.get("regional_summary", {})
    summary_parts = []
    for region_key in REGION_ORDER:
        text = regional_summary.get(region_key, "")
        if text:
            region_name, emoji = REGION_CONFIG[region_key]
            text_html = markdown_to_html(text)
            summary_parts.append(f'    <p><span class="region">{emoji} {region_name}:</span> {text_html}</p>')
    summary_html = "\n".join(summary_parts)

    # Render must_know
    must_know_html = "\n".join(
        render_article(article, include_reporting_varies=True) for article in selections.get("must_know", [])
    )

    # Render should_know
    should_know_html = "\n".join(
        render_article(article, include_reporting_varies=False) for article in selections.get("should_know", [])
    )

    # Render signals (clustered by region)
    signals = selections.get("signals", {})
    cluster_parts = []
    for region_key in REGION_ORDER:
        items = signals.get(region_key, [])
        if items:
            region_name, emoji = REGION_CONFIG[region_key]
            region_id = region_key.replace("_", "-")
            cluster_parts.append(f'    <div id="{region_id}" class="cluster">')
            cluster_parts.append(f"      <h3>{emoji} {region_name}</h3>")
            for item in items:
                cluster_parts.append(render_signal(item))
            cluster_parts.append("    </div>")
    signals_html = "\n".join(cluster_parts)

    # Fill template
    result = template
    result = result.replace("{{REGIONAL_SUMMARY}}", summary_html)
    result = result.replace("{{MUST_KNOW}}", must_know_html)
    result = result.replace("{{SHOULD_KNOW}}", should_know_html)
    result = result.replace("{{SIGNALS}}", signals_html)

    return result


def extract_headlines(selections: dict, get_source_id_fn) -> list[dict]:
    """Extract all headlines from selections for deduplication tracking.

    Args:
        selections: The selections dict from Claude
        get_source_id_fn: Function to map source name to source_id
    """
    headlines = []

    def get_first_source_id(item: dict) -> str | None:
        """Get source_id from first source in item's sources list."""
        sources = item.get("sources", [])
        if sources:
            name = sources[0].get("name", "")
            return get_source_id_fn(name)
        # For signals, source is a single object, not a list
        source = item.get("source", {})
        if source:
            name = source.get("name", "")
            return get_source_id_fn(name)
        return None

    # Top-tier articles (must_know, should_know)
    for tier in ["must_know", "should_know"]:
        for item in selections.get(tier, []):
            headlines.append(
                {
                    "headline": item.get("headline", ""),
                    "tier": tier,
                    "source_id": get_first_source_id(item),
                }
            )

    # Signals by cluster
    signals = selections.get("signals", {})
    for cluster in REGION_ORDER:
        for item in signals.get(cluster, []):
            headlines.append(
                {
                    "headline": item.get("headline", ""),
                    "tier": "signal",
                    "cluster": cluster,
                    "source_id": get_first_source_id(item),
                }
            )

    return headlines


def extract_preheader(selections: dict, max_length: int = 150) -> str:
    """Extract preheader text from first regional summary for email preview."""
    regional_summary = selections.get("regional_summary", {})
    for region in REGION_ORDER:
        summary = regional_summary.get(region, "")
        if summary:
            # Strip markdown links, get plain text
            plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
            # Get first sentence or truncate
            first_sentence = plain.split(".")[0] + "."
            if len(first_sentence) <= max_length:
                return first_sentence
            return plain[:max_length].rsplit(" ", 1)[0] + "..."
    return ""


def format_story_counts(selections: dict) -> str:
    """Format story counts per section for meta line."""
    must = len(selections.get("must_know", []))
    should = len(selections.get("should_know", []))
    signals = sum(len(region) for region in selections.get("signals", {}).values())
    return f"{must} 🥇 · {should} 🥈 · {signals} 🥉"


def replace_placeholders(
    digest_path: Path,
    selections: dict,
    styles_file: Path,
    preheader: str = "",
    log_fn=None,
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
    model_name = os.environ.get("MODEL_NAME", "Claude")
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
    content = content.replace("{{READING_TIME}}", calculate_reading_time(content))
    content = content.replace("{{STORY_COUNT}}", story_count_str)
    content = content.replace("{{GENERATED_AT}}", generated_at)
    content = content.replace("{{MODEL_NAME}}", html.escape(model_name))
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
        content = re.sub(r"\s*<p>\{\{AUTHOR_PLUG\}\}</p>", "", content)

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
        content = re.sub(r'<a href="\{\{ARCHIVE_URL\}\}">[^<]+</a> · ', "", content)

    digest_path.write_text(content)
    if log_fn:
        log_fn(f"Date: {date_str}")
