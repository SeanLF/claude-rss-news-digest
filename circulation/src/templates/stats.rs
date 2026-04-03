//! Stats dashboard template.

use std::collections::HashMap;

use super::digest::{ANCHOR_SVG, FAVICON_SVG, SECTION_ANCHOR_CSS};
use crate::stats::StatsData;
use crate::util::escape_html;

/// Render the stats dashboard page
pub fn render_stats(name: &str, days: u32, data: &StatsData) -> String {
    let health_rows = build_health_rows(data);
    let usage_rows = build_usage_rows(data);
    let runs_rows = build_runs_rows(data);
    let dedup_row = build_dedup_row(data);
    let never_selected_content = build_never_selected(data);

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stats – {name}</title>
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
      --green: #2d7a3a;
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
        --green: #4aba5a;
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
    .container {{
      max-width: 900px;
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
    .period-select {{
      margin-bottom: 2.5rem;
      display: flex;
      gap: 0.5rem;
    }}
    .period-select a {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      display: inline-block;
      padding: 0.4rem 0.85rem;
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text-muted);
      text-decoration: none;
      font-size: 0.8rem;
      font-weight: 500;
      transition: border-color 0.15s ease, color 0.15s ease;
    }}
    .period-select a:hover {{
      border-color: var(--ruby);
      color: var(--text);
    }}
    .period-select a.active {{
      background: var(--ruby);
      color: white;
      border-color: var(--ruby);
    }}
    section {{
      margin-bottom: 3rem;
    }}
    h2 {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      margin: 0 0 1rem;
      position: relative;
      scroll-margin-top: 3.5rem;
    }}
    {section_anchor_css}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 0.85rem;
    }}
    th, td {{
      padding: 0.6rem 0.75rem;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }}
    th {{
      font-weight: 600;
      color: var(--text-muted);
      font-size: 0.8rem;
    }}
    td {{
      color: var(--text);
    }}
    td.empty, p.empty {{
      color: var(--text-muted);
      font-style: italic;
      text-align: center;
    }}
    .good {{ color: var(--green); }}
    .warn {{ color: var(--yellow); }}
    .bad {{ color: var(--ruby); }}
    .section-note {{
      color: var(--text-muted);
      font-size: 0.88rem;
      margin-bottom: 0.75rem;
    }}
    .source-list {{
      color: var(--ink-light);
      font-size: 0.88rem;
      line-height: 1.6;
    }}
    @media (max-width: 600px) {{
      .container {{
        padding: 3rem 1.25rem 5rem;
      }}
      h1 {{
        font-size: 1.75rem;
      }}
      table {{
        font-size: 0.75rem;
      }}
      th, td {{
        padding: 0.5rem;
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
    <a href="/" class="back-link">← Back to digests</a>
    <h1>Stats</h1>
    <p class="subtitle">Source health and usage over the last {days} days</p>

    <div class="period-select">
      <a href="/stats?days=7"{active7}>7 days</a>
      <a href="/stats?days=30"{active30}>30 days</a>
      <a href="/stats?days=90"{active90}>90 days</a>
    </div>

    <section>
      <h2 id="source-health"><a class="section-anchor" href="#source-health" aria-label="Link to Source Health">{anchor_svg}</a>Source Health</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Fetches</th>
            <th scope="col">Successes</th>
            <th scope="col">Rate</th>
          </tr>
        </thead>
        <tbody>
          {health_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2 id="source-usage"><a class="section-anchor" href="#source-usage" aria-label="Link to Source Usage">{anchor_svg}</a>Source Usage in Digests</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Must Know</th>
            <th scope="col">Should Know</th>
            <th scope="col">Other</th>
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          {usage_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2 id="recent-runs"><a class="section-anchor" href="#recent-runs" aria-label="Link to Recent Runs">{anchor_svg}</a>Recent Runs</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Time (UTC)</th>
            <th scope="col">Articles Fetched</th>
            <th scope="col">Recipients</th>
            <th scope="col">API cost equiv.</th>
          </tr>
        </thead>
        <tbody>
          {runs_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2 id="dedup"><a class="section-anchor" href="#dedup" aria-label="Link to Dedup Effectiveness">{anchor_svg}</a>Dedup Effectiveness</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Articles Filtered</th>
            <th scope="col">Avg Similarity</th>
            <th scope="col">Min</th>
            <th scope="col">Max</th>
          </tr>
        </thead>
        <tbody>
          {dedup_row}
        </tbody>
      </table>
    </section>

    <section>
      <h2 id="never-selected"><a class="section-anchor" href="#never-selected" aria-label="Link to Never Selected">{anchor_svg}</a>Never Selected</h2>
      <p class="section-note">Sources fetched but never included in digests</p>
      {never_selected_content}
    </section>
  </div>
  </main>
</body>
</html>"##,
        favicon = FAVICON_SVG,
        active7 = if days == 7 { " class=\"active\"" } else { "" },
        active30 = if days == 30 { " class=\"active\"" } else { "" },
        active90 = if days == 90 { " class=\"active\"" } else { "" },
        skip_link_html = super::digest::SKIP_LINK_HTML,
        skip_link_css = super::digest::SKIP_LINK_CSS,
        reduced_motion_css = super::digest::REDUCED_MOTION_CSS,
        anchor_svg = ANCHOR_SVG,
        section_anchor_css = SECTION_ANCHOR_CSS,
    )
}

// --- Helper functions for building table rows ---

fn build_health_rows(data: &StatsData) -> String {
    if data.source_health.is_empty() {
        return r#"<tr><td colspan="4" class="empty">No data yet</td></tr>"#.to_string();
    }

    data.source_health
        .iter()
        .map(|h| {
            let status_class = if h.success_rate_pct >= 95.0 {
                "good"
            } else if h.success_rate_pct >= 80.0 {
                "warn"
            } else {
                "bad"
            };
            format!(
                r#"<tr>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td class="{}">{:.0}%</td>
                </tr>"#,
                escape_html(&h.source_id),
                h.total_fetches,
                h.successes,
                status_class,
                h.success_rate_pct
            )
        })
        .collect()
}

fn build_usage_rows(data: &StatsData) -> String {
    if data.source_usage.is_empty() {
        return r#"<tr><td colspan="5" class="empty">No data yet</td></tr>"#.to_string();
    }

    // Aggregate by source across tiers
    let mut usage_by_source: HashMap<String, (i64, i64, i64)> = HashMap::new();
    for u in &data.source_usage {
        let entry = usage_by_source.entry(u.source_id.clone()).or_default();
        match u.tier.as_str() {
            "must_know" => entry.0 += u.count,
            "should_know" => entry.1 += u.count,
            _ => entry.2 += u.count,
        }
    }

    let mut usage_sorted: Vec<_> = usage_by_source.into_iter().collect();
    usage_sorted.sort_by(|a, b| {
        let total_a = a.1.0 + a.1.1 + a.1.2;
        let total_b = b.1.0 + b.1.1 + b.1.2;
        total_b.cmp(&total_a)
    });

    usage_sorted
        .iter()
        .map(|(source_id, (must, should, other))| {
            let total = must + should + other;
            format!(
                r#"<tr>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td><strong>{}</strong></td>
                </tr>"#,
                escape_html(source_id),
                must,
                should,
                other,
                total
            )
        })
        .collect()
}

fn build_runs_rows(data: &StatsData) -> String {
    if data.recent_runs.is_empty() {
        return r#"<tr><td colspan="4" class="empty">No runs yet</td></tr>"#.to_string();
    }

    data.recent_runs
        .iter()
        .map(|r| {
            let cost_cell = match r.api_cost_usd {
                Some(c) => format!("${:.2}", c),
                None => "—".to_string(),
            };
            format!(
                r#"<tr>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                </tr>"#,
                r.run_at, r.articles_fetched, r.articles_emailed, cost_cell
            )
        })
        .collect()
}

fn build_dedup_row(data: &StatsData) -> String {
    match &data.dedup_stats {
        Some(d) => format!(
            r#"<tr>
                <td>{}</td>
                <td>{:.2}</td>
                <td>{:.2}</td>
                <td>{:.2}</td>
            </tr>"#,
            d.filtered_count, d.avg_similarity, d.min_similarity, d.max_similarity
        ),
        None => r#"<tr><td colspan="4" class="empty">No dedup data yet</td></tr>"#.to_string(),
    }
}

fn build_never_selected(data: &StatsData) -> String {
    if data.never_selected.is_empty() {
        r#"<p class="empty">All sources have been selected at least once</p>"#.to_string()
    } else {
        let escaped: Vec<String> = data.never_selected.iter().map(|s| escape_html(s)).collect();
        format!(r#"<p class="source-list">{}</p>"#, escaped.join(", "))
    }
}
