//! Search page template - full-text search over shown headlines.

use super::digest::{FAVICON_SVG, REDUCED_MOTION_CSS, SKIP_LINK_CSS, SKIP_LINK_HTML};
use crate::search::SearchResult;
use crate::util::{escape_html, format_date};

/// Human label for a tier value. Unknown/blank tiers fall back to the raw
/// (escaped) value rather than hiding the badge -- new tiers shouldn't need a
/// template change to show up.
fn tier_label(tier: &str) -> String {
    match tier {
        "must_know" => "Must Know".to_string(),
        "should_know" => "Should Know".to_string(),
        "" => String::new(),
        other => escape_html(other),
    }
}

fn build_results_html(results: &[SearchResult]) -> String {
    results
        .iter()
        .map(|r| {
            let headline = escape_html(&r.headline);
            let tier_html = {
                let label = tier_label(&r.tier);
                if label.is_empty() {
                    String::new()
                } else {
                    format!(r#"<span class="tier-badge">{label}</span>"#)
                }
            };
            match &r.date {
                Some(date) => {
                    let formatted = format_date(date);
                    format!(
                        r#"<li><a href="/{date}">{tier_html}<span class="result-headline">{headline}</span><span class="result-date">{formatted}</span></a></li>"#
                    )
                }
                // No digest row to link to (shouldn't happen in practice) --
                // render as plain text instead of a dead link.
                None => format!(
                    r#"<li class="result-unlinked">{tier_html}<span class="result-headline">{headline}</span></li>"#
                ),
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Render the search page. `query` is `None` when no `q` param was given at
/// all (landing state); `Some("")` never happens -- the handler already
/// collapses blank queries to `None` via `sanitize_query`.
pub fn render_search(name: &str, query: Option<&str>, results: &[SearchResult]) -> String {
    let body = match query {
        None => {
            r#"<p class="search-hint">Enter a search term above to look through past headlines.</p>"#
                .to_string()
        }
        Some(q) if results.is_empty() => {
            let escaped_q = escape_html(q);
            format!(r#"<p class="search-hint">No results for &ldquo;{escaped_q}&rdquo;.</p>"#)
        }
        Some(_) => {
            format!(
                r#"<ul class="search-results">
{}
    </ul>"#,
                build_results_html(results)
            )
        }
    };

    let query_value = query.map(escape_html).unwrap_or_default();

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Search – {name}</title>
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
      max-width: 640px;
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
      max-width: 640px;
      margin: 0 auto;
      padding: 1.5rem 1.5rem 7rem;
    }}
    h1 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 2rem;
      font-weight: 700;
      margin: 0 0 1rem;
      letter-spacing: -0.025em;
      color: var(--text);
    }}
    .search-form {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }}
    .search-form input {{
      flex: 1;
      padding: 0.6rem 0.85rem;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text);
      font-family: inherit;
      font-size: 0.93rem;
    }}
    .search-form input:focus-visible {{
      border-color: var(--ruby);
    }}
    .search-form button {{
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
    .search-form button:hover {{
      background: var(--ruby-hover);
    }}
    .search-hint {{
      color: var(--text-muted);
      font-size: 0.93rem;
    }}
    .search-tip {{
      color: var(--text-muted);
      font-size: 0.8rem;
      margin: 0 0 2rem;
    }}
    ul.search-results {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .search-results li {{
      border-top: 1px solid var(--border);
    }}
    .search-results li:last-child {{
      border-bottom: 1px solid var(--border);
    }}
    .search-results li a {{
      display: block;
      padding: 0.85rem 0;
      color: var(--text);
      text-decoration: none;
      transition: background 0.15s ease;
    }}
    .search-results li a:hover {{
      background: rgba(196, 90, 59, 0.03);
    }}
    .result-unlinked {{
      padding: 0.85rem 0;
      color: var(--ink-light);
    }}
    .tier-badge {{
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.65rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-right: 0.5rem;
    }}
    .result-headline {{
      font-weight: 600;
      font-size: 0.95rem;
    }}
    .result-date {{
      display: block;
      font-size: 0.82rem;
      color: var(--text-muted);
      margin-top: 0.15rem;
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
    <h1>Search</h1>
    <form class="search-form" action="/search" method="get" role="search">
      <input type="search" name="q" value="{query_value}" placeholder="Search past headlines" aria-label="Search past headlines">
      <button type="submit">Search</button>
    </form>
    <p class="search-tip">Matches words and phrases literally &mdash; boolean operators (AND, OR, NOT, *) aren&rsquo;t supported.</p>
    {body}
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        skip_link_html = SKIP_LINK_HTML,
        skip_link_css = SKIP_LINK_CSS,
        reduced_motion_css = REDUCED_MOTION_CSS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_headline_in_results() {
        let results = vec![SearchResult {
            headline: "<script>alert(1)</script>".to_string(),
            tier: "must_know".to_string(),
            date: Some("2026-07-01".to_string()),
        }];
        let html = render_search("Digest", Some("test"), &results);
        assert!(!html.contains("<script>alert(1)</script>"));
        assert!(html.contains("&lt;script&gt;alert(1)&lt;/script&gt;"));
    }

    #[test]
    fn escapes_query_value_in_input() {
        let html = render_search("Digest", Some("\"><img src=x onerror=alert(1)>"), &[]);
        assert!(!html.contains("<img src=x"));
    }

    #[test]
    fn landing_state_has_no_results_list() {
        let html = render_search("Digest", None, &[]);
        assert!(html.contains("Enter a search term"));
        assert!(!html.contains("<ul class=\"search-results\">"));
    }

    #[test]
    fn no_results_state_shows_honest_copy() {
        let html = render_search("Digest", Some("volcano"), &[]);
        assert!(html.contains("No results for"));
        assert!(html.contains("volcano"));
    }

    #[test]
    fn shows_literal_match_hint() {
        let html = render_search("Digest", None, &[]);
        assert!(html.contains("search-tip"));
        assert!(html.contains("boolean operators"));
    }

    #[test]
    fn unlinked_result_has_no_dead_link() {
        let results = vec![SearchResult {
            headline: "Orphaned headline".to_string(),
            tier: "should_know".to_string(),
            date: None,
        }];
        let html = render_search("Digest", Some("orphan"), &results);
        assert!(html.contains("result-unlinked"));
        assert!(!html.contains("<a href=\"/\">Orphaned"));
    }
}
