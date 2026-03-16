//! Sources page template - lists news sources with bias and factuality ratings.

use super::digest::FAVICON_SVG;
use crate::routes;
use crate::util::escape_html;

/// A single source entry for rendering.
pub struct Source {
    pub name: String,
    pub website: String,
    pub bias: String,
    pub factuality: String,
    pub perspective: String,
    pub feed_count: u32,
    pub mbfc_slug: String,
}

/// Render the sources page
pub fn render_sources(name: &str, sources: &[Source], source_data_url: Option<&str>) -> String {
    // Group by bias
    let (lean_left, centre, lean_right): (Vec<&Source>, Vec<&Source>, Vec<&Source>) = {
        let mut ll = Vec::new();
        let mut c = Vec::new();
        let mut lr = Vec::new();
        for s in sources {
            match s.bias.as_str() {
                "lean-left" => ll.push(s),
                "center" => c.push(s),
                "lean-right" => lr.push(s),
                _ => c.push(s), // fallback
            }
        }
        (ll, c, lr)
    };

    let total_outlets = sources.len();
    let total_feeds: u32 = sources.iter().map(|s| s.feed_count).sum();

    let ll_html = build_source_list(&lean_left);
    let c_html = build_source_list(&centre);
    let lr_html = build_source_list(&lean_right);

    let ll_count = lean_left.len();
    let c_count = centre.len();
    let lr_count = lean_right.len();

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sources – {name}</title>
  {favicon}
  <style>
    :root {{
      --bg: #fafaf8;
      --text: #1c1c1a;
      --text-muted: #6b6b67;
      --ruby: #c45a3b;
      --ruby-hover: #d4897a;
      --border: #e0e0da;
      --ink-light: #4a4a46;
      --green: #5a9e4b;
      --green-deep: #2d7a3a;
      --yellow: #a67c00;
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
        --green: #6dbf5e;
        --green-deep: #4aba5a;
        --yellow: #eab308;
      }}
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    html {{
      font-size: 18px;
      background-color: var(--bg);
      scroll-behavior: smooth;
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
    .page-nav {{
      max-width: 760px;
      margin: 0 auto;
      padding: 12px 1.5rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 14px;
    }}
    .page-nav a {{
      color: var(--text-muted);
      text-decoration: none;
    }}
    .page-nav a:hover {{
      color: var(--ruby);
    }}
    .container {{
      max-width: 760px;
      margin: 0 auto;
      padding: 2rem 1.5rem 7rem;
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
    .column-header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 1rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.65rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      padding: 0 0 0.3rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: -1px;
    }}
    .spectrum-wrapper {{
      margin: 1.5rem 0 2.5rem;
    }}
    .bias-spectrum {{
      display: flex;
      align-items: stretch;
      gap: 0;
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.72rem;
      letter-spacing: 0.03em;
      color: var(--text-muted);
    }}
    .bias-spectrum > a,
    .bias-spectrum > span {{
      flex: 1;
      text-align: center;
      padding: 0.5rem 0.2rem;
      border-bottom: 3px solid var(--border);
      transition: border-color 0.15s, color 0.15s;
      text-decoration: none !important;
      color: var(--text-muted);
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .bias-spectrum a.active {{
      border-bottom-color: var(--ruby);
      color: var(--text);
      font-weight: 600;
    }}
    .bias-spectrum .empty {{
      opacity: 0.35;
    }}
    .bias-spectrum a:hover {{
      border-bottom-color: var(--ruby-hover);
      color: var(--ruby-hover);
    }}
    .bias-spectrum .count {{
      display: block;
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 0.15rem;
    }}
    .bias-spectrum .empty .count {{
      color: var(--text-muted);
    }}
    .bias-spectrum .label-emoji {{
      font-size: 0.8rem;
      margin-bottom: 0.1rem;
    }}
    h2 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      margin: 2.5rem 0 0.75rem;
      padding-bottom: 0.25rem;
      border-bottom: 1px solid var(--border);
      scroll-margin-top: 3.5rem;
    }}
    h2:first-of-type {{
      margin-top: 0;
    }}
    .section-count {{
      font-weight: 400;
      opacity: 0.6;
    }}
    .source-list {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .source-item {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: baseline;
      padding: 0.55rem 0;
      gap: 1rem;
    }}
    .source-name {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-weight: 600;
      font-size: 0.93rem;
    }}
    .source-name a {{
      color: var(--text);
      text-decoration: none;
    }}
    .source-name a:hover {{
      color: var(--ruby);
    }}
    .feed-note {{
      color: var(--text-muted);
      font-weight: 400;
      font-size: 0.82rem;
    }}
    .source-perspective {{
      font-size: 0.82rem;
      color: var(--text-muted);
      margin-top: 0.1rem;
    }}
    .source-meta {{
      display: flex;
      gap: 0.5rem;
      align-items: center;
      flex-shrink: 0;
    }}
    a.badge {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.7rem;
      font-weight: 600;
      letter-spacing: 0.03em;
      padding: 0.2rem 0.5rem;
      border-radius: 3px;
      white-space: nowrap;
      text-decoration: none;
      transition: border-color 0.15s, color 0.15s;
    }}
    a.badge-factuality {{
      color: var(--green);
      border: 1px solid rgba(90, 158, 75, 0.3);
      background: rgba(90, 158, 75, 0.06);
    }}
    a.badge-factuality:hover {{
      border-color: var(--green);
    }}
    a.badge-factuality.very-high {{
      color: var(--green-deep);
      border-color: rgba(45, 122, 58, 0.35);
      background: rgba(45, 122, 58, 0.1);
    }}
    a.badge-factuality.very-high:hover {{
      border-color: var(--green-deep);
    }}
    a.badge-factuality.mixed {{
      color: var(--yellow);
      border-color: rgba(166, 124, 0, 0.25);
      background: rgba(166, 124, 0, 0.06);
    }}
    a.badge-factuality.mixed:hover {{
      border-color: var(--yellow);
    }}
    .methodology {{
      margin-top: 3rem;
      font-size: 0.88rem;
      color: var(--text-muted);
      line-height: 1.6;
    }}
    .methodology p {{
      margin: 0.5rem 0;
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
    @media (max-width: 640px) {{
      .bias-spectrum {{
        font-size: 0.6rem;
      }}
      .bias-spectrum .label-emoji {{
        font-size: 0.7rem;
      }}
    }}
    @media (max-width: 480px) {{
      .container {{
        padding: 1.5rem 1.25rem 5rem;
      }}
      h1 {{
        font-size: 1.75rem;
      }}
      .source-item {{
        grid-template-columns: 1fr;
        gap: 0.3rem;
      }}
      .source-meta {{
        justify-content: flex-start;
      }}
      .bias-spectrum {{
        font-size: 0.5rem;
      }}
      .bias-spectrum .label-emoji {{
        font-size: 0.7rem;
      }}
    }}
    {skip_link_css}
    {reduced_motion_css}
  </style>
</head>
<body>
  {skip_link_html}
  <nav class="page-nav">
    <a href="/">&#8592; Past digests</a>
  </nav>
  <main id="main">
  <div class="container">
    <h1>Sources</h1>
    <p class="tagline">{total_outlets} outlets, {total_feeds} feeds.</p>
    <div class="meta-links">
      <a class="meta-link" href="/">Archive</a> &middot;
      <a class="meta-link" href="{stats_url}">Stats</a> &middot;
      <a class="meta-link" href="https://ground.news/rating-system">Rating methodology</a>
    </div>

    <div class="spectrum-wrapper">
    <nav class="bias-spectrum" aria-label="Bias distribution">
      <span class="empty" aria-hidden="true">
        <span class="label-emoji">&#x21E0;</span>
        <span class="count">0</span>
        Far left
      </span>
      <span class="empty" aria-hidden="true">
        <span class="label-emoji">&#x2190;</span>
        <span class="count">0</span>
        Left
      </span>
      <a href="#lean-left" class="{ll_class}">
        <span class="label-emoji">&#x2039;</span>
        <span class="count">{ll_count}</span>
        Lean left
      </a>
      <a href="#centre" class="{c_class}">
        <span class="label-emoji">&#x25CB;</span>
        <span class="count">{c_count}</span>
        Centre
      </a>
      <a href="#lean-right" class="{lr_class}">
        <span class="label-emoji">&#x203A;</span>
        <span class="count">{lr_count}</span>
        Lean right
      </a>
      <span class="empty" aria-hidden="true">
        <span class="label-emoji">&#x2192;</span>
        <span class="count">0</span>
        Right
      </span>
      <span class="empty" aria-hidden="true">
        <span class="label-emoji">&#x21E2;</span>
        <span class="count">0</span>
        Far right
      </span>
    </nav>
    </div>

    <h2 id="lean-left"><span aria-hidden="true">&#x2039;</span> Lean left <span class="section-count">{ll_count} outlets</span></h2>
    <div class="column-header"><span>Source</span><span>Factuality</span></div>
    <ul class="source-list">
{ll_html}
    </ul>

    <h2 id="centre"><span aria-hidden="true">&#x25CB;</span> Centre <span class="section-count">{c_count} outlets</span></h2>
    <ul class="source-list">
{c_html}
    </ul>

    <h2 id="lean-right"><span aria-hidden="true">&#x203A;</span> Lean right <span class="section-count">{lr_count} outlets</span></h2>
    <ul class="source-list">
{lr_html}
    </ul>

    <div class="methodology">
      <p>Bias and factuality ratings aggregated from <a href="https://ground.news/rating-system">Ground News</a>, which combines assessments from <a href="https://www.allsides.com/">AllSides</a>, <a href="https://adfontesmedia.com/">Ad Fontes Media</a>, and <a href="https://mediabiasfactcheck.com/">Media Bias/Fact Check</a>. Perspective labels reflect each outlet's geographic or editorial vantage point.</p>
      <p>The digest draws from sources across the political spectrum to show how different outlets cover the same stories &mdash; inspired by <a href="https://ground.news">Ground News</a>.{source_data_link}</p>
    </div>

    <footer class="site-footer">
      <p><a href="/">Archive</a> &middot; <a href="{stats_url}">Stats</a> &middot; <a href="https://seanfloyd.dev">seanfloyd.dev</a></p>
    </footer>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        stats_url = routes::STATS,
        ll_class = if ll_count > 0 { "active" } else { "empty" },
        c_class = if c_count > 0 { "active" } else { "empty" },
        lr_class = if lr_count > 0 { "active" } else { "empty" },
        skip_link_html = super::digest::SKIP_LINK_HTML,
        skip_link_css = super::digest::SKIP_LINK_CSS,
        reduced_motion_css = super::digest::REDUCED_MOTION_CSS,
        source_data_link = match source_data_url {
            Some(url) => format!(
                r#" View the <a href="{url}/blob/main/newsroom/sources.json">raw source data</a> on GitHub."#
            ),
            None => String::new(),
        },
    )
}

fn build_source_list(sources: &[&Source]) -> String {
    sources
        .iter()
        .map(|s| {
            let name = escape_html(&s.name);
            let website = escape_html(&s.website);
            let perspective = escape_html(&s.perspective);
            let mbfc_url = if s.mbfc_slug.is_empty() {
                "https://mediabiasfactcheck.com/".to_string()
            } else {
                format!("https://mediabiasfactcheck.com/{}/", escape_html(&s.mbfc_slug))
            };

            let feed_note = if s.feed_count > 1 {
                format!(r#" <span class="feed-note">({} feeds)</span>"#, s.feed_count)
            } else {
                String::new()
            };

            let badge_html = match s.factuality.as_str() {
                "unrated" => String::new(),
                factuality => {
                    let (label, class) = match factuality {
                        "very-high" => ("Very high", " very-high"),
                        "mixed" => ("Mixed", " mixed"),
                        _ => ("High", ""),
                    };
                    format!(
                        r#"<a class="badge badge-factuality{class}" href="{mbfc_url}" target="_blank" rel="noopener" aria-label="{label} factuality — view {name} on MBFC">{label}</a>"#
                    )
                }
            };

            format!(
                r#"      <li class="source-item">
        <div>
          <div class="source-name"><a href="{website}" target="_blank" rel="noopener">{name}</a>{feed_note}</div>
          <div class="source-perspective">{perspective}</div>
        </div>
        <div class="source-meta">
          {badge_html}
        </div>
      </li>"#
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}
