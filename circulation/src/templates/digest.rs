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
.nav-sections {
    display: flex;
    gap: 16px;
}
.nav-sections a {
    font-size: 0.85em;
    letter-spacing: 0.03em;
}
.email-only,
.header-links {
    display: none;
}
.web-only {
    display: block;
}
@media (max-width: 480px) {
    .nav-sections { display: none; }
    article h3 .anchor { display: none; }
}
</style>"#;

/// HTML injected into digest pages for browser navigation
pub const DIGEST_NAV_HTML: &str = r##"<nav class="digest-nav">
    <a href="/">&#8592; Past digests</a>
    <div class="nav-sections">
        <a href="#must-know">Must Know</a>
        <a href="#should-know">Should Know</a>
    </div>
</nav>"##;

/// Web footer links injected into digest pages (replaces email-only unsubscribe with subscribe)
pub fn web_footer_html(
    subscribe_url: &str,
    archive_url: &str,
    sources_url: &str,
    privacy_url: &str,
) -> String {
    format!(
        r#"<p class="footer-links web-only"><a href="{archive_url}">Past digests</a> · <a href="{sources_url}">Sources</a> · <a href="{privacy_url}">Privacy</a> · <a href="{subscribe_url}">Subscribe</a></p>"#
    )
}

/// Build OG meta tags for a digest page
pub fn digest_og_tags(
    title: &str,
    description: &str,
    canonical_url: &str,
    site_name: &str,
) -> String {
    format!(
        r#"<meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{canonical_url}">
  <meta property="og:site_name" content="{site_name}">
  <meta name="description" content="{description}">"#
    )
}
