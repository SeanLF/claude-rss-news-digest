//! Digest page template - navigation injected into stored HTML.

/// CSS injected into digest pages for browser navigation
pub const DIGEST_NAV_CSS: &str = r#"<style>
.digest-nav {
    max-width: 820px;
    margin: 0 auto;
    padding: 12px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
}
.digest-nav a {
    color: var(--text-muted, #777);
    text-decoration: none;
}
.digest-nav a:hover {
    color: var(--accent, #c45a3b);
}
</style>"#;

/// HTML injected into digest pages for browser navigation
pub const DIGEST_NAV_HTML: &str = r#"<nav class="digest-nav">
    <a href="/">← All digests</a>
</nav>"#;
