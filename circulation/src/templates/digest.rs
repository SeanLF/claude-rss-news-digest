//! Digest page template - navigation and web-specific elements injected into stored HTML.

/// SVG link icon for section anchor links -- shared across pages.
pub const ANCHOR_SVG: &str = r#"<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M7.775 3.275a.75.75 0 0 0 1.06 1.06l1.25-1.25a2 2 0 1 1 2.83 2.83l-2.5 2.5a2 2 0 0 1-2.83 0 .75.75 0 0 0-1.06 1.06 3.5 3.5 0 0 0 4.95 0l2.5-2.5a3.5 3.5 0 0 0-4.95-4.95l-1.25 1.25zm-4.69 9.64a2 2 0 0 1 0-2.83l2.5-2.5a2 2 0 0 1 2.83 0 .75.75 0 0 0 1.06-1.06 3.5 3.5 0 0 0-4.95 0l-2.5 2.5a3.5 3.5 0 0 0 4.95 4.95l1.25-1.25a.75.75 0 0 0-1.06-1.06l-1.25 1.25a2 2 0 0 1-2.83 0z"/></svg>"#;

/// Favicon as inline SVG data URI (terracotta document icon)
pub const FAVICON_SVG: &str = r#"<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23c45a3b'/%3E%3Cline x1='8' y1='10' x2='24' y2='10' stroke='white' stroke-width='2.5' stroke-linecap='round'/%3E%3Cline x1='8' y1='16' x2='20' y2='16' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.7'/%3E%3Cline x1='8' y1='22' x2='16' y2='22' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.4'/%3E%3C/svg%3E">"#;

/// Skip-to-content link CSS -- shared across all pages.
pub const SKIP_LINK_CSS: &str = r#"
.skip-link {
    position: absolute;
    left: -9999px;
    top: auto;
    width: 1px;
    height: 1px;
    overflow: hidden;
    z-index: 1000;
    padding: 0.75rem 1.5rem;
    background: var(--bg, #fafaf8);
    color: var(--ruby, #c45a3b);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 0.9rem;
    text-decoration: none;
    border: 2px solid var(--ruby, #c45a3b);
    border-radius: 4px;
}
.skip-link:focus-visible {
    position: fixed;
    left: 1rem;
    top: 1rem;
    width: auto;
    height: auto;
    overflow: visible;
}"#;

/// Section anchor link CSS -- shared across pages with anchored headings.
pub const SECTION_ANCHOR_CSS: &str = r#"
    .section-anchor {
      position: absolute;
      left: -1.2em;
      top: 50%;
      transform: translateY(-50%);
      opacity: 0;
      text-decoration: none;
      transition: opacity 0.15s ease;
      color: var(--text-muted);
      line-height: 1;
    }
    .section-anchor svg {
      width: 0.75em;
      height: 0.75em;
      fill: currentColor;
      display: block;
    }
    h2:hover .section-anchor,
    .section-anchor:focus-visible {
      opacity: 1;
    }
    @media (max-width: 640px) {
      .section-anchor { display: none; }
    }"#;

/// Reduced-motion support CSS -- shared across all pages.
pub const REDUCED_MOTION_CSS: &str = r#"
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
    }
    html { scroll-behavior: auto; }
}"#;

/// Skip-to-content link HTML -- first child of <body> on every page.
pub const SKIP_LINK_HTML: &str = r##"<a href="#main" class="skip-link">Skip to content</a>"##;

/// CSS injected into digest pages for browser navigation and web-specific overrides.
/// Includes retroactive style improvements that apply to all stored digests.
pub const DIGEST_NAV_CSS: &str = r#"<style>
.digest-nav {
    max-width: 820px;
    margin: 0 auto;
    padding: 12px 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 14px;
    /* Sans utility row against the serif body -- matches the mockup. */
    font-family: system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
.digest-nav a {
    color: var(--text-muted, #767676);
    text-decoration: none;
}
.digest-nav a:hover {
    color: var(--accent, #c45a3b);
}
.digest-nav a:focus-visible {
    outline: 2px solid var(--accent, #c45a3b);
    outline-offset: 2px;
}
.nav-right {
    display: flex;
    align-items: center;
    gap: 16px;
}
.nav-sections {
    display: flex;
    gap: 16px;
}
.nav-sections a {
    font-size: 0.85em;
    letter-spacing: 0.03em;
}
/* Translate control -- a terracotta pill (mockup placement 01).
   Selector is `.digest-nav a.nav-translate` so its terracotta colour beats the
   muted `.digest-nav a` rule (which would otherwise grey out the pill text). */
.digest-nav a.nav-translate {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.8rem;
    font-weight: 550;
    color: var(--accent, #c45a3b);
    text-decoration: none;
    border: 1px solid color-mix(in srgb, var(--accent, #c45a3b) 45%, transparent);
    /* Warm opaque fill (mockup's --terra-soft), mixed with the digest's own bg so
       it adapts to light/dark across every stored digest. */
    background: color-mix(in srgb, var(--accent, #c45a3b) 14%, var(--bg, #fafaf8));
    border-radius: 999px;
    padding: 0.3rem 0.7rem 0.3rem 0.55rem;
    transition: background 0.15s ease, border-color 0.15s ease;
}
.digest-nav a.nav-translate:hover {
    color: var(--accent, #c45a3b);
    background: color-mix(in srgb, var(--accent, #c45a3b) 22%, var(--bg, #fafaf8));
    border-color: var(--accent, #c45a3b);
}
.nav-translate .glyph {
    font-weight: 700;
    font-size: 0.9em;
    letter-spacing: -0.03em;
    line-height: 1;
}
/* Hide the pill once the reader is on Google's translate.goog proxy -- redundant
   there (Google's own language bar is the backstop). Two signals: Google's own
   translated-* class on <html>, and the via-proxy hostname flag (head script). */
html.translated-ltr .nav-translate,
html.translated-rtl .nav-translate,
.via-proxy .nav-translate {
    display: none;
}
/* Feedback invitation -- its own line, clearly clickable, so it doesn't read as
   just another nav link. */
.footer-feedback {
    margin: 12px 0 4px;
    color: var(--text-muted, #767676);
}
.footer-feedback a {
    color: var(--accent, #c45a3b);
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 2px;
}
.email-only,
.header-links {
    display: none;
}
.web-only {
    display: block;
}
@media (max-width: 480px) {
    /* Hide the section anchors on mobile, but keep the translate pill. */
    .nav-sections { display: none; }
    article h3 .anchor { display: none; }
}
</style>"#;

/// Injected into the digest `<head>`: when the page is being viewed through
/// Google's `translate.goog` proxy, flag the document (`via-proxy`) so the
/// on-page Translate control hides itself. The reader is already translated and
/// Google's own language bar is the backstop (spec §2); showing our pill there is
/// redundant and would re-translate. Runs in `<head>` to avoid a flash. Belt and
/// suspenders with the `html.translated-*` CSS rule (Google's own marker).
pub const PROXY_TRANSLATE_HIDE_SCRIPT: &str = r#"<script>if(location.hostname.indexOf("translate.goog")>-1)document.documentElement.className+=" via-proxy";</script>"#;

/// Browser-nav bar injected at the top of digest pages. Carries the web-only
/// `文A Translate` control (spec §3.2): links to `/{date}/translate`, styled in
/// the product's terracotta, engine-agnostic (Google self-brands on the
/// destination page).
pub fn digest_nav_html(date: &str) -> String {
    format!(
        r##"<nav class="digest-nav">
    <a href="/">&#8592; Past digests</a>
    <div class="nav-right">
        <a class="nav-translate" href="/{date}/translate" aria-label="Translate this page"><span class="glyph" aria-hidden="true">文A</span> Translate</a>
        <span class="nav-sections">
            <a href="#must-know">Must Know</a>
            <a href="#should-know">Should Know</a>
        </span>
    </div>
</nav>"##
    )
}

/// Web footer links injected into digest pages (replaces email-only unsubscribe
/// with subscribe). When `feedback_email` is set, appends a clean "Feedback"
/// `mailto:` link (spec §4) to the footer row, with the digest date prefilled in
/// the subject.
pub fn web_footer_html(
    subscribe_url: &str,
    archive_url: &str,
    sources_url: &str,
    privacy_url: &str,
    date: &str,
    feedback_email: Option<&str>,
) -> String {
    // date is a validated YYYY-MM-DD and feedback_email is trusted config
    // (RESEND_FROM), so the mailto is safe to build without extra encoding. Its own
    // invitation line (not a middot nav link) so the feedback channel is obvious --
    // the web parallel to the email's "just hit reply".
    let feedback = feedback_email
        .map(|email| {
            format!(
                r#"<p class="footer-feedback web-only">Got feedback or a suggestion? <a href="mailto:{email}?subject=Digest%20feedback%20-%20{date}">Send a note &rarr;</a></p>"#
            )
        })
        .unwrap_or_default();
    format!(
        r#"<p class="footer-links web-only"><a href="{archive_url}">Past digests</a> · <a href="{sources_url}">Sources</a> · <a href="{privacy_url}">Privacy</a> · <a href="{subscribe_url}">Subscribe</a></p>{feedback}"#
    )
}

/// Build OG meta tags for a digest page
pub fn digest_og_tags(
    title: &str,
    description: &str,
    canonical_url: &str,
    site_name: &str,
    image_url: &str,
) -> String {
    format!(
        r#"<meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{canonical_url}">
  <meta property="og:site_name" content="{site_name}">
  <meta name="description" content="{description}">
  {image_tags}"#,
        image_tags = og_image_tags(image_url)
    )
}

/// Build og:image / twitter:card meta tags shared by every page with OG tags.
pub fn og_image_tags(image_url: &str) -> String {
    format!(
        r#"<meta property="og:image" content="{image_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">"#
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_og_tags_includes_absolute_image_and_twitter_card() {
        let html = digest_og_tags(
            "Title",
            "Description",
            "https://example.com/2026-07-01",
            "News Digest",
            "https://example.com/og-image.png",
        );
        assert!(
            html.contains(
                r#"<meta property="og:image" content="https://example.com/og-image.png">"#
            )
        );
        assert!(html.contains(r#"<meta property="og:image:width" content="1200">"#));
        assert!(html.contains(r#"<meta property="og:image:height" content="630">"#));
        assert!(html.contains(r#"<meta name="twitter:card" content="summary_large_image">"#));
    }

    #[test]
    fn og_image_tags_uses_the_given_absolute_url() {
        let html = og_image_tags("https://example.com/og-image.png");
        assert!(html.contains(r#"content="https://example.com/og-image.png""#));
    }

    #[test]
    fn digest_nav_includes_translate_pill_for_the_date() {
        let html = digest_nav_html("2026-07-03");
        assert!(
            html.contains(r#"href="/2026-07-03/translate""#),
            "nav should link the date's translate route: {html}"
        );
        // The mockup's pill: a `nav-translate` chip with the 文A glyph (文 tinted).
        assert!(
            html.contains(r#"class="nav-translate"#),
            "translate is a pill"
        );
        assert!(html.contains("文"), "nav should carry the 文A glyph");
        // The pill sits before the section links (mockup placement 01).
        let pill = html.find("nav-translate").unwrap();
        let must = html.find("#must-know").unwrap();
        assert!(pill < must, "translate pill precedes the section links");
        assert!(html.contains("#should-know"));
    }

    #[test]
    fn web_footer_adds_mailto_feedback_when_address_configured() {
        let html = web_footer_html(
            "/#subscribe",
            "/",
            "/sources",
            "/privacy",
            "2026-07-03",
            Some("daily-news-digest@seanfloyd.dev"),
        );
        // A dedicated invitation line -- NOT buried as another middot nav link --
        // with a clear call-to-action and the date prefilled in the subject.
        assert!(
            html.contains(r#"class="footer-feedback"#),
            "own line: {html}"
        );
        assert!(html.contains("feedback or a suggestion"));
        assert!(html.contains(r#"mailto:daily-news-digest@seanfloyd.dev?subject="#));
        assert!(html.contains("2026-07-03"));
        // The feedback affordance must not sit inside the utilitarian links row.
        let links_row = html.split("</p>").next().unwrap();
        assert!(
            !links_row.contains("mailto:"),
            "feedback should not be in the footer-links row: {html}"
        );
    }

    #[test]
    fn translate_pill_hides_when_viewed_through_the_google_proxy() {
        // Two independent signals so the now-redundant pill goes away once the
        // reader is on translate.goog: Google's own translated-* class on <html>,
        // and a hostname flag set by the injected head script.
        assert!(
            DIGEST_NAV_CSS.contains("html.translated-ltr .nav-translate"),
            "should hide on Google's translated-ltr marker"
        );
        assert!(DIGEST_NAV_CSS.contains(".via-proxy .nav-translate"));
        assert!(
            PROXY_TRANSLATE_HIDE_SCRIPT.contains("translate.goog"),
            "head script must sniff the proxy hostname"
        );
        assert!(PROXY_TRANSLATE_HIDE_SCRIPT.contains("via-proxy"));
    }

    #[test]
    fn web_footer_omits_feedback_when_no_address() {
        let html = web_footer_html(
            "/#subscribe",
            "/",
            "/sources",
            "/privacy",
            "2026-07-03",
            None,
        );
        assert!(
            !html.contains("mailto:"),
            "no address -> no feedback link: {html}"
        );
    }
}
