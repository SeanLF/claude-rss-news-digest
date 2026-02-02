//! Stats dashboard template.

use std::collections::HashMap;

use crate::stats::StatsData;

/// Render the stats dashboard page
pub fn render_stats(name: &str, css_link: &str, days: u32, data: &StatsData) -> String {
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
  {css_link}
  <style>
    .container {{
      max-width: 900px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
    }}
    h1 {{
      font-size: 1.75rem;
      font-weight: 700;
      margin-bottom: 0.25rem;
      letter-spacing: -0.02em;
    }}
    .subtitle {{
      color: var(--text-tertiary);
      margin-bottom: 2rem;
    }}
    .period-select {{
      margin-bottom: 2rem;
    }}
    .period-select a {{
      display: inline-block;
      padding: 0.5rem 1rem;
      margin-right: 0.5rem;
      background: var(--bg-card);
      border: 1px solid var(--border-white-subtle);
      border-radius: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      font-size: 0.875rem;
    }}
    .period-select a:hover,
    .period-select a.active {{
      border-color: var(--ruby-red);
      color: var(--text-primary);
    }}
    .period-select a.active {{
      background: var(--ruby-red);
      color: white;
      border-color: var(--ruby-red);
    }}
    section {{
      margin-bottom: 3rem;
    }}
    h2 {{
      font-size: 1rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-tertiary);
      margin-bottom: 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
    }}
    th, td {{
      padding: 0.75rem 1rem;
      text-align: left;
      border-bottom: 1px solid var(--border-white-subtle);
    }}
    th {{
      background: var(--bg-card);
      font-weight: 600;
      color: var(--text-secondary);
    }}
    td {{
      color: var(--text-primary);
    }}
    td.empty, p.empty {{
      color: var(--text-tertiary);
      font-style: italic;
      text-align: center;
    }}
    .good {{ color: var(--accent-green, #22c55e); }}
    .warn {{ color: var(--accent-yellow, #eab308); }}
    .bad {{ color: var(--ruby-red); }}
    .back-link {{
      display: inline-block;
      margin-bottom: 1.5rem;
      color: var(--text-tertiary);
      text-decoration: none;
      font-size: 0.875rem;
    }}
    .back-link:hover {{
      color: var(--ruby-red);
    }}
    .section-note {{
      color: var(--text-tertiary);
      font-size: 0.875rem;
      margin-bottom: 0.75rem;
    }}
    .source-list {{
      color: var(--text-secondary);
      font-size: 0.875rem;
      line-height: 1.6;
    }}
    @media (max-width: 600px) {{
      table {{
        font-size: 0.75rem;
      }}
      th, td {{
        padding: 0.5rem;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <a href="/" class="back-link">← Back to digests</a>
    <h1>Stats</h1>
    <p class="subtitle">Source health and usage over the last {days} days</p>

    <div class="period-select">
      <a href="/stats?days=7"{}>7 days</a>
      <a href="/stats?days=30"{}>30 days</a>
      <a href="/stats?days=90"{}>90 days</a>
    </div>

    <section>
      <h2>Source Health</h2>
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Fetches</th>
            <th>Successes</th>
            <th>Rate</th>
          </tr>
        </thead>
        <tbody>
          {health_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Source Usage in Digests</h2>
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Must Know</th>
            <th>Should Know</th>
            <th>Other</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {usage_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Recent Runs</h2>
      <table>
        <thead>
          <tr>
            <th>Time (UTC)</th>
            <th>Articles Fetched</th>
            <th>Recipients</th>
          </tr>
        </thead>
        <tbody>
          {runs_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Dedup Effectiveness</h2>
      <table>
        <thead>
          <tr>
            <th>Articles Filtered</th>
            <th>Avg Similarity</th>
            <th>Min</th>
            <th>Max</th>
          </tr>
        </thead>
        <tbody>
          {dedup_row}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Never Selected</h2>
      <p class="section-note">Sources fetched but never included in digests</p>
      {never_selected_content}
    </section>
  </div>
</body>
</html>"##,
        if days == 7 { " class=\"active\"" } else { "" },
        if days == 30 { " class=\"active\"" } else { "" },
        if days == 90 { " class=\"active\"" } else { "" },
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
                h.source_id, h.total_fetches, h.successes, status_class, h.success_rate_pct
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
                source_id, must, should, other, total
            )
        })
        .collect()
}

fn build_runs_rows(data: &StatsData) -> String {
    if data.recent_runs.is_empty() {
        return r#"<tr><td colspan="3" class="empty">No runs yet</td></tr>"#.to_string();
    }

    data.recent_runs
        .iter()
        .map(|r| {
            format!(
                r#"<tr>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                </tr>"#,
                r.run_at, r.articles_fetched, r.articles_emailed
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
        format!(
            r#"<p class="source-list">{}</p>"#,
            data.never_selected.join(", ")
        )
    }
}
