//! Thread history page template - a public read-only view of an evolving story-thread.

use super::digest::FAVICON_SVG;
use crate::routes;
use crate::thread::{ThreadDetail, ThreadSummary};
use crate::util::{escape_html, format_date};

/// Human label + CSS class for a thread's `status` column.
fn status_badge(status: &str) -> (&'static str, &'static str) {
    match status {
        "active" => ("Ongoing", "status-active"),
        "dormant" => ("Dormant", "status-dormant"),
        "closed" => ("Closed", "status-closed"),
        _ => ("Unknown", "status-dormant"),
    }
}

/// Render one thread's history page: title, status, then entries newest-first.
pub fn render_thread(name: &str, thread: &ThreadDetail) -> String {
    let (status_label, status_class) = status_badge(&thread.status);
    let label = escape_html(&thread.label);
    let entry_count = thread.entries.len();
    let entries_html = if thread.entries.is_empty() {
        r#"<p class="empty">No history recorded for this thread yet.</p>"#.to_string()
    } else {
        thread
            .entries
            .iter()
            .map(build_entry)
            .collect::<Vec<_>>()
            .join("\n")
    };

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{label} – {name}</title>
  {favicon}
  <style>
{root_css}
    .container {{
      max-width: 700px;
      margin: 0 auto;
      padding: 4.5rem 1.5rem 7rem;
    }}
    .back-link {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.88rem;
      color: var(--text-muted);
      text-decoration: none;
      display: inline-block;
      margin-bottom: 2rem;
    }}
    h1 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 2rem;
      font-weight: 700;
      margin: 0 0 0.5rem;
      letter-spacing: -0.025em;
      color: var(--text);
    }}
    .status-badge {{
      display: inline-block;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      padding: 0.2rem 0.5rem;
      border-radius: 3px;
    }}
    .status-active {{
      color: var(--green);
      background: rgba(45, 122, 58, 0.08);
    }}
    .status-dormant, .status-closed {{
      color: var(--text-muted);
      background: rgba(107, 107, 103, 0.1);
    }}
    .subtitle {{
      color: var(--text-muted);
      margin: 0.75rem 0 2.5rem;
      font-size: 0.9rem;
    }}
    .thread-history {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .thread-history li {{
      padding: 1.1rem 0;
      border-top: 1px solid var(--border);
    }}
    .thread-history li:last-child {{
      border-bottom: 1px solid var(--border);
    }}
    .entry-date {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-weight: 600;
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 0.3rem;
    }}
    .entry-date a {{
      color: var(--text-muted);
      text-decoration: none;
    }}
    .entry-date a:hover {{
      color: var(--ruby);
    }}
    .entry-headline {{
      font-weight: 600;
      margin-bottom: 0.2rem;
    }}
    .entry-delta {{
      color: var(--ink-light);
      margin: 0.3rem 0 0;
    }}
    p.empty {{
      color: var(--text-muted);
      font-style: italic;
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
    @media (max-width: 480px) {{
      .container {{
        padding: 3rem 1.25rem 5rem;
      }}
      h1 {{
        font-size: 1.6rem;
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
    <a href="{threads_url}" class="back-link">&#8592; All threads</a>
    <h1>{label}</h1>
    <p class="subtitle"><span class="status-badge {status_class}">{status_label}</span> &middot; {entry_count} update{entry_plural}</p>
    <ol class="thread-history">
{entries_html}
    </ol>
    <footer class="site-footer">
      <p><a href="/">Archive</a> &middot; <a href="{threads_url}">Threads</a> &middot; <a href="{sources_url}">Sources</a> &middot; <a href="{stats_url}">Stats</a></p>
    </footer>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        root_css = ROOT_CSS,
        skip_link_html = super::digest::SKIP_LINK_HTML,
        skip_link_css = super::digest::SKIP_LINK_CSS,
        reduced_motion_css = super::digest::REDUCED_MOTION_CSS,
        threads_url = routes::THREADS,
        sources_url = routes::SOURCES,
        stats_url = routes::STATS,
        entry_plural = if entry_count == 1 { "" } else { "s" },
    )
}

fn build_entry(entry: &crate::thread::ThreadEntry) -> String {
    let formatted_date = format_date(&entry.day);
    let date_html = match &entry.digest_date {
        Some(d) => format!(r#"<a href="/{}">{}</a>"#, escape_html(d), formatted_date),
        None => formatted_date,
    };
    let headline = escape_html(&entry.cluster_story);
    let delta_html = if entry.delta.is_empty() {
        String::new()
    } else {
        format!(
            r#"<p class="entry-delta">{}</p>"#,
            escape_html(&entry.delta)
        )
    };

    format!(
        r#"      <li>
        <div class="entry-date">{date_html}</div>
        <div class="entry-headline">{headline}</div>
        {delta_html}
      </li>"#
    )
}

/// Render the `/threads` index: active threads first, then dormant/closed, each newest-updated
/// first within its group.
pub fn render_threads_index(name: &str, threads: &[ThreadSummary]) -> String {
    let rows_html = if threads.is_empty() {
        r#"<p class="empty">No threads yet.</p>"#.to_string()
    } else {
        threads
            .iter()
            .map(build_summary_row)
            .collect::<Vec<_>>()
            .join("\n")
    };

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Threads – {name}</title>
  {favicon}
  <style>
{root_css}
    .container {{
      max-width: 700px;
      margin: 0 auto;
      padding: 4.5rem 1.5rem 7rem;
    }}
    .back-link {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.88rem;
      color: var(--text-muted);
      text-decoration: none;
      display: inline-block;
      margin-bottom: 2rem;
    }}
    h1 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 2.25rem;
      font-weight: 700;
      margin: 0 0 0.3rem;
      letter-spacing: -0.025em;
      color: var(--text);
    }}
    .subtitle {{
      color: var(--text-muted);
      margin-bottom: 2.5rem;
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
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      padding: 0.85rem 0;
      color: var(--text);
      text-decoration: none;
      transition: background 0.15s ease;
    }}
    li a:hover {{
      background: rgba(196, 90, 59, 0.03);
    }}
    .thread-label {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-weight: 600;
      font-size: 0.93rem;
    }}
    .thread-updated {{
      font-size: 0.82rem;
      color: var(--text-muted);
      white-space: nowrap;
    }}
    p.empty {{
      color: var(--text-muted);
      font-style: italic;
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
    @media (max-width: 480px) {{
      .container {{
        padding: 3rem 1.25rem 5rem;
      }}
      h1 {{
        font-size: 1.75rem;
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
    <a href="/" class="back-link">&#8592; Past digests</a>
    <h1>Threads</h1>
    <p class="subtitle">Ongoing stories tracked across days.</p>
    <ul>
{rows_html}
    </ul>
    <footer class="site-footer">
      <p><a href="/">Archive</a> &middot; <a href="{sources_url}">Sources</a> &middot; <a href="{stats_url}">Stats</a></p>
    </footer>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        root_css = ROOT_CSS,
        skip_link_html = super::digest::SKIP_LINK_HTML,
        skip_link_css = super::digest::SKIP_LINK_CSS,
        reduced_motion_css = super::digest::REDUCED_MOTION_CSS,
        sources_url = routes::SOURCES,
        stats_url = routes::STATS,
    )
}

fn build_summary_row(t: &ThreadSummary) -> String {
    let (status_label, _) = status_badge(&t.status);
    let label = escape_html(&t.label);
    let updated = t.updated_at.get(..10).unwrap_or(&t.updated_at);
    let updated_display = format_date(updated);
    format!(
        r#"      <li><a href="{}/{}"><span class="thread-label">{label}</span><span class="thread-updated">{status_label} &middot; {updated_display}</span></a></li>"#,
        routes::THREAD,
        t.id
    )
}

/// Shared `:root` colour variables + base typography, copied from `sources.rs`/`stats.rs`
/// (each page in this crate is a standalone document, so the block is duplicated by convention
/// rather than centralised).
const ROOT_CSS: &str = r#"
    :root {
      --bg: #fafaf8;
      --text: #1c1c1a;
      --text-muted: #6b6b67;
      --ruby: #c45a3b;
      --ruby-hover: #d4897a;
      --border: #e0e0da;
      --ink-light: #4a4a46;
      --green: #2d7a3a;
      color-scheme: light dark;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #141412;
        --text: #e6e6e2;
        --text-muted: #9a9a94;
        --ruby: #e07a5f;
        --ruby-hover: #f0a08a;
        --border: #2c2c28;
        --ink-light: #b0b0aa;
        --green: #4aba5a;
      }
    }
    *, *::before, *::after { box-sizing: border-box; }
    html {
      font-size: 18px;
      background-color: var(--bg);
      scroll-behavior: smooth;
    }
    body {
      color: var(--text);
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.58;
      margin: 0;
      padding: 0;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }
    ::selection {
      background: rgba(196, 90, 59, 0.15);
      color: inherit;
    }
    :focus-visible {
      outline: 2px solid var(--ruby);
      outline-offset: 2px;
    }
    a {
      color: var(--ruby);
      text-decoration: underline;
      text-decoration-color: transparent;
      text-underline-offset: 3px;
      text-decoration-thickness: 1px;
      transition: color 0.15s ease, text-decoration-color 0.2s ease;
    }
    a:hover {
      color: var(--ruby-hover);
      text-decoration-color: var(--ruby-hover);
    }"#;
