//! Digest page template - navigation and web-specific elements injected into stored HTML.

/// Favicon as inline SVG data URI (terracotta document icon)
pub const FAVICON_SVG: &str = r#"<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23c45a3b'/%3E%3Cline x1='8' y1='10' x2='24' y2='10' stroke='white' stroke-width='2.5' stroke-linecap='round'/%3E%3Cline x1='8' y1='16' x2='20' y2='16' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.7'/%3E%3Cline x1='8' y1='22' x2='16' y2='22' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.4'/%3E%3C/svg%3E">"#;

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
    color: var(--text-muted, #777);
    text-decoration: none;
}
.digest-nav a:hover {
    color: var(--accent, #c45a3b);
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
}
</style>"#;

/// HTML injected into digest pages for browser navigation
pub const DIGEST_NAV_HTML: &str = r##"<nav class="digest-nav">
    <a href="/">&#8592; Past digests</a>
    <div class="nav-sections">
        <a href="#must-know">Must Know</a>
        <a href="#should-know">Should Know</a>
        <a href="#also-notable">Also Notable</a>
    </div>
</nav>"##;

/// Web footer links injected into digest pages (replaces email-only unsubscribe with subscribe)
pub fn web_footer_html(subscribe_url: &str, archive_url: &str) -> String {
    format!(
        r#"<p class="footer-links web-only"><a href="{archive_url}">Past digests</a> · <a href="{subscribe_url}">Subscribe</a></p>"#
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
