//! Index page template - lists recent digests.

use super::digest::{FAVICON_SVG, og_image_tags};

/// Parameters for rendering the index page.
pub struct IndexParams<'a> {
    pub name: &'a str,
    pub meta_links: &'a str,
    pub success_msg: &'a str,
    pub subscribe_form: &'a str,
    pub subscribe_teaser: &'a str,
    pub digest_links: &'a str,
    pub og_description: &'a str,
    pub canonical_url: &'a str,
    pub image_url: &'a str,
}

/// Render the index page listing recent digests
pub fn render_index(p: &IndexParams) -> String {
    let IndexParams {
        name,
        meta_links,
        success_msg,
        subscribe_form,
        subscribe_teaser,
        digest_links,
        og_description,
        canonical_url,
        image_url,
    } = p;
    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name}</title>
  {favicon}
  <meta property="og:title" content="{name}">
  <meta property="og:description" content="{og_description}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{canonical_url}">
  <meta property="og:site_name" content="{name}">
  <meta name="description" content="{og_description}">
  {image_tags}
  <style>
    :root {{
      --bg: #fafaf8;
      --text: #1c1c1a;
      --text-muted: #6b6b67;
      --ruby: #c45a3b;
      --ruby-hover: #d4897a;
      --border: #e0e0da;
      --ink-light: #4a4a46;
      --green: #2d7a3a;
      --green-bg: rgba(45, 122, 58, 0.08);
      color-scheme: light dark;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #141412;
        --text: #e6e6e2;
        --text-muted: #9a9a94;
        --ruby: #e07a5f;
        --ruby-hover: #f0a08a;
        --border: #2c2c28;
        --ink-light: #b0b0aa;
        --green: #4aba5a;
        --green-bg: rgba(74, 186, 90, 0.08);
      }}
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    html {{
      font-size: 18px;
      background-color: var(--bg);
    }}
    body {{
      color: var(--text);
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.58;
      margin: 0;
      padding: 0;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }}
    ::selection {{
      background: rgba(196, 90, 59, 0.15);
      color: inherit;
    }}
    :focus-visible {{
      outline: 2px solid var(--ruby);
      outline-offset: 2px;
    }}
    a {{
      color: var(--ruby);
      text-decoration: underline;
      text-decoration-color: transparent;
      text-underline-offset: 3px;
      text-decoration-thickness: 1px;
      transition: color 0.15s ease, text-decoration-color 0.2s ease;
    }}
    a:hover {{
      color: var(--ruby-hover);
      text-decoration-color: var(--ruby-hover);
    }}
    .container {{
      max-width: 640px;
      margin: 0 auto;
      padding: 4.5rem 1.5rem 7rem;
    }}
    h1 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 2.25rem;
      font-weight: 700;
      margin: 0 0 0.3rem;
      letter-spacing: -0.025em;
      color: var(--text);
    }}
    .tagline {{
      color: var(--text-muted);
      margin: 0 0 0.5rem;
    }}
    .meta-links {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.88rem;
      color: var(--text-muted);
      margin-bottom: 2rem;
    }}
    .meta-link {{
      color: var(--text-muted);
      text-decoration: none;
    }}
    .meta-link:hover {{
      color: var(--ruby);
    }}
    .success-msg {{
      color: var(--green);
      background: var(--green-bg);
      padding: 0.75rem 1rem;
      border-radius: 4px;
      margin-bottom: 1.5rem;
      border-left: 3px solid var(--green);
      font-size: 0.93rem;
    }}
    .subscribe-teaser {{
      color: var(--text-muted);
      font-size: 0.93rem;
      margin-bottom: 2.5rem;
    }}
    .subscribe-teaser a {{
      color: var(--ruby);
      text-decoration: none;
    }}
    .subscribe-form {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }}
    .subscribe-form input {{
      flex: 1;
      padding: 0.6rem 0.85rem;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text);
      font-family: inherit;
      font-size: 0.93rem;
    }}
    .subscribe-form input::placeholder {{
      color: var(--text-muted);
    }}
    .subscribe-form input:focus-visible {{
      border-color: var(--ruby);
    }}
    .subscribe-form button {{
      padding: 0.6rem 1.25rem;
      background: var(--ruby);
      color: white;
      border: none;
      border-radius: 4px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-weight: 600;
      font-size: 0.88rem;
      cursor: pointer;
      transition: background 0.15s ease;
    }}
    .subscribe-form button:hover {{
      background: var(--ruby-hover);
    }}
    h2 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      margin: 0 0 1rem;
    }}
    .digest-archive details {{
      margin-bottom: 0.5rem;
    }}
    .month-heading {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      margin: 1rem 0 0;
      padding-bottom: 0.25rem;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      list-style: none;
    }}
    .month-heading::-webkit-details-marker {{
      display: none;
    }}
    .month-heading::before {{
      content: "▸ ";
      font-size: 0.7rem;
    }}
    details[open] > .month-heading::before {{
      content: "▾ ";
    }}
    details:first-child .month-heading {{
      margin-top: 0;
    }}
    details[open] ul li:first-child {{
      border-top: none;
    }}
    ul {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    li {{
      border-top: 1px solid var(--border);
    }}
    li:last-child {{
      border-bottom: 1px solid var(--border);
    }}
    li a {{
      display: block;
      padding: 0.85rem 0;
      color: var(--text);
      text-decoration: none;
      transition: background 0.15s ease;
    }}
    li a:hover {{
      background: rgba(196, 90, 59, 0.03);
    }}
    .date-text {{
      display: block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-weight: 600;
      font-size: 0.93rem;
    }}
    .preheader-text {{
      display: block;
      font-size: 0.88rem;
      color: var(--ink-light);
      margin-top: 0.2rem;
      line-height: 1.5;
    }}
    .site-footer {{
      margin-top: 3rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.8rem;
      color: var(--text-muted);
    }}
    .site-footer a {{
      color: var(--text-muted);
      text-decoration: none;
    }}
    .site-footer a:hover {{
      color: var(--ruby);
    }}
    .site-footer p {{
      margin: 0.25rem 0;
    }}
    @media (max-width: 480px) {{
      .container {{
        padding: 3rem 1.25rem 5rem;
      }}
      h1 {{
        font-size: 1.75rem;
      }}
      .subscribe-form {{
        flex-direction: column;
      }}
      .subscribe-form button {{
        width: 100%;
      }}
    }}
    {skip_link_css}
    {reduced_motion_css}
  </style>
</head>
<body>
  {skip_link_html}
  <main id="main">
  <div class="container">
    <h1>{name}</h1>
    <p class="tagline">{og_description}</p>
    {meta_links}
    {success_msg}
    {subscribe_form}
    {subscribe_teaser}
    <h2>All Digests</h2>
    <div class="digest-archive">
      {digest_links}
    </div>
    <footer class="site-footer">
      <p><a href="https://seanfloyd.dev/privacy">Privacy Policy</a></p>
      <p>&copy; Sean Floyd</p>
    </footer>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        image_tags = og_image_tags(image_url),
        skip_link_html = super::digest::SKIP_LINK_HTML,
        skip_link_css = super::digest::SKIP_LINK_CSS,
        reduced_motion_css = super::digest::REDUCED_MOTION_CSS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_params() -> IndexParams<'static> {
        IndexParams {
            name: "News Digest",
            meta_links: "",
            success_msg: "",
            subscribe_form: "",
            subscribe_teaser: "",
            digest_links: "",
            og_description: "Daily briefing.",
            canonical_url: "https://example.com",
            image_url: "https://example.com/og-image.png",
        }
    }

    #[test]
    fn render_index_includes_absolute_og_image_and_twitter_card() {
        let html = render_index(&base_params());
        assert!(
            html.contains(
                r#"<meta property="og:image" content="https://example.com/og-image.png">"#
            )
        );
        assert!(html.contains(r#"<meta property="og:image:width" content="1200">"#));
        assert!(html.contains(r#"<meta property="og:image:height" content="630">"#));
        assert!(html.contains(r#"<meta name="twitter:card" content="summary_large_image">"#));
    }
}
