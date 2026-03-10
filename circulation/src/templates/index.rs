//! Index page template - lists recent digests.

use super::digest::FAVICON_SVG;

/// Render the index page listing recent digests
#[allow(clippy::too_many_arguments)]
pub fn render_index(
    name: &str,
    css_link: &str,
    meta_links: &str,
    success_msg: &str,
    subscribe_form: &str,
    subscribe_teaser: &str,
    digest_links: &str,
    og_description: &str,
    canonical_url: &str,
) -> String {
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
  {css_link}
  <style>
    .container {{
      max-width: 600px;
      margin: 0 auto;
      padding: 3rem 1.5rem;
    }}
    h1 {{
      font-size: 2rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }}
    .tagline {{
      color: var(--text-tertiary);
      margin-bottom: 0.5rem;
    }}
    .meta-links {{
      color: var(--text-tertiary);
      font-size: 0.875rem;
      margin-bottom: 1.5rem;
    }}
    .meta-link {{
      color: var(--text-tertiary);
      text-decoration: none;
      transition: color 0.2s ease;
    }}
    .meta-link:hover {{
      color: #c45a3b;
    }}
    .success-msg {{
      color: var(--accent-green);
      background: var(--accent-green-bg);
      padding: 0.75rem 1rem;
      border-radius: 0.5rem;
      margin-bottom: 1.5rem;
      border-left: 3px solid var(--accent-green);
    }}
    .subscribe-teaser {{
      color: var(--text-tertiary);
      font-size: 0.875rem;
      margin-bottom: 2rem;
    }}
    .subscribe-teaser a {{
      color: #c45a3b;
      text-decoration: none;
    }}
    .subscribe-teaser a:hover {{
      text-decoration: underline;
    }}
    .subscribe-form {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }}
    .subscribe-form input {{
      flex: 1;
      padding: 0.75rem 1rem;
      background: var(--bg-card);
      border: 1px solid var(--border-white-light);
      border-radius: 0.5rem;
      color: var(--text-primary);
      font-size: 1rem;
    }}
    .subscribe-form input::placeholder {{
      color: var(--text-tertiary);
    }}
    .subscribe-form input:focus {{
      outline: none;
      border-color: #c45a3b;
    }}
    .subscribe-form button {{
      padding: 0.75rem 1.5rem;
      background: linear-gradient(135deg, #c45a3b 0%, #d4897a 100%);
      color: white;
      border: none;
      border-radius: 0.5rem;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .subscribe-form button:hover {{
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(204, 52, 45, 0.3);
    }}
    h2 {{
      font-size: 1rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-tertiary);
      margin-bottom: 1rem;
    }}
    .digest-archive details {{
      margin-bottom: 0.5rem;
    }}
    .month-heading {{
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-tertiary);
      margin: 1rem 0 0.5rem 0;
      padding-bottom: 0.25rem;
      border-bottom: 1px solid var(--border-white-subtle);
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
    ul {{
      list-style: none;
      padding: 0;
    }}
    li {{
      margin: 0.5rem 0;
    }}
    li a {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      padding: 0.75rem 1rem;
      background: var(--bg-card);
      border: 1px solid var(--border-white-subtle);
      border-radius: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      transition: all 0.2s ease;
    }}
    li a:hover {{
      border-color: #c45a3b;
      color: var(--text-primary);
      transform: translateX(4px);
    }}
    .arrow {{
      color: var(--text-tertiary);
      transition: transform 0.2s ease, color 0.2s ease;
    }}
    li a:hover .arrow {{
      color: #c45a3b;
      transform: translateX(4px);
    }}
    .link-content {{
      flex: 1;
      min-width: 0;
    }}
    .date-text {{
      display: block;
      font-weight: 500;
      color: var(--text-primary);
    }}
    .preheader-text {{
      display: block;
      font-size: 0.82rem;
      color: var(--text-tertiary);
      margin-top: 0.25rem;
      line-height: 1.4;
    }}
    .site-footer {{
      margin-top: 2rem;
      font-size: 0.85rem;
      opacity: 0.5;
    }}
    .site-footer a {{
      color: inherit;
      text-decoration: none;
    }}
    @media (max-width: 480px) {{
      .subscribe-form {{
        flex-direction: column;
      }}
      .subscribe-form button {{
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
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
</body>
</html>"##,
        favicon = FAVICON_SVG,
    )
}
