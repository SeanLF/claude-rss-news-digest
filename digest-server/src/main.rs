use axum::{
    Form, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{Html, Redirect},
    routing::{get, post},
};
use reqwest::Client;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

struct AppState {
    db_path: String,
    digest_name: String,
    css_url: Option<String>,
    homepage_url: Option<String>,
    source_url: Option<String>,
    resend_api_key: Option<String>,
    resend_audience_id: Option<String>,
    http_client: Client,
}

#[derive(Deserialize)]
struct SubscribeForm {
    email: String,
}

#[derive(Deserialize, Default)]
struct IndexQuery {
    subscribed: Option<String>,
}

#[derive(Serialize)]
struct ResendContact {
    email: String,
}

/// Index page - lists recent digests
async fn index(
    State(state): State<Arc<AppState>>,
    Query(query): Query<IndexQuery>,
) -> Result<Html<String>, (StatusCode, String)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Get list of available digests (most recent first)
    let mut stmt = conn
        .prepare("SELECT date FROM digests ORDER BY date DESC LIMIT 30")
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?;

    let dates: Vec<String> = stmt
        .query_map([], |row| row.get(0))
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| r.ok())
        .collect();

    let links: String = dates
        .iter()
        .map(|d| {
            let formatted = format_date(d);
            format!(r#"<li><a href="/{d}"><span class="date-text">{formatted}</span><span class="arrow">→</span></a></li>"#)
        })
        .collect::<Vec<_>>()
        .join("\n      ");

    let name = &state.digest_name;
    let success_msg = if query.subscribed.is_some() {
        r#"<div class="success-msg">Thanks for subscribing! You'll receive the next digest.</div>"#
    } else {
        ""
    };
    let subscriptions_enabled =
        state.resend_api_key.is_some() && state.resend_audience_id.is_some();
    let subscribe_form = if subscriptions_enabled {
        r#"<form method="post" action="/subscribe" class="subscribe-form">
        <input type="email" name="email" placeholder="your@email.com" required>
        <button type="submit">Subscribe</button>
      </form>"#
    } else {
        ""
    };
    let homepage_link = state.homepage_url.as_ref().map(|url| {
        let display = url
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        format!(r#"<a href="{url}" class="meta-link">{display}</a>"#)
    });
    let source_link = state.source_url.as_ref().map(|_| {
        format!(
            r#"<a href="{}" class="meta-link">GitHub</a>"#,
            state.source_url.as_ref().unwrap()
        )
    });
    let stats_link = r#"<a href="/stats" class="meta-link">Stats</a>"#;
    let meta_links = match (homepage_link, source_link) {
        (Some(h), Some(s)) => format!(r#"<p class="meta-links">{h} · {s} · {stats_link}</p>"#),
        (Some(h), None) => format!(r#"<p class="meta-links">{h} · {stats_link}</p>"#),
        (None, Some(s)) => format!(r#"<p class="meta-links">{s} · {stats_link}</p>"#),
        (None, None) => format!(r#"<p class="meta-links">{stats_link}</p>"#),
    };
    let css_link = state
        .css_url
        .as_ref()
        .map(|url| format!(r#"<link rel="stylesheet" href="{url}">"#))
        .unwrap_or_default();
    let html = format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name}</title>
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
      color: var(--ruby-red);
    }}
    .success-msg {{
      color: var(--accent-green);
      background: var(--accent-green-bg);
      padding: 0.75rem 1rem;
      border-radius: 0.5rem;
      margin-bottom: 1.5rem;
      border-left: 3px solid var(--accent-green);
    }}
    .subscribe-form {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 2rem;
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
      border-color: var(--ruby-red);
    }}
    .subscribe-form button {{
      padding: 0.75rem 1.5rem;
      background: linear-gradient(135deg, var(--ruby-red) 0%, var(--ruby-red-light) 100%);
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
    ul {{
      list-style: none;
    }}
    li {{
      margin: 0.5rem 0;
    }}
    li a {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem 1rem;
      background: var(--bg-card);
      border: 1px solid var(--border-white-subtle);
      border-radius: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      transition: all 0.2s ease;
    }}
    li a:hover {{
      border-color: var(--ruby-red);
      color: var(--text-primary);
      transform: translateX(4px);
    }}
    .arrow {{
      color: var(--text-tertiary);
      transition: transform 0.2s ease, color 0.2s ease;
    }}
    li a:hover .arrow {{
      color: var(--ruby-red);
      transform: translateX(4px);
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
    <p class="tagline">Daily briefing on geopolitics, tech, and privacy. All sides. No fluff.</p>
    {meta_links}
    {success_msg}
    {subscribe_form}
    <h2>Recent Digests</h2>
    <ul>
      {links}
    </ul>
  </div>
</body>
</html>"##
    );

    Ok(Html(html))
}

/// Subscribe handler - adds email to Resend audience
async fn subscribe(
    State(state): State<Arc<AppState>>,
    Form(form): Form<SubscribeForm>,
) -> Result<Redirect, (StatusCode, String)> {
    let (api_key, audience_id) = state
        .resend_api_key
        .as_ref()
        .zip(state.resend_audience_id.as_ref())
        .ok_or((
            StatusCode::SERVICE_UNAVAILABLE,
            "Subscriptions not configured".into(),
        ))?;

    let url = format!("https://api.resend.com/audiences/{}/contacts", audience_id);

    let response = state
        .http_client
        .post(&url)
        .header("Authorization", format!("Bearer {}", api_key))
        .json(&ResendContact { email: form.email })
        .send()
        .await
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Request failed: {e}"),
            )
        })?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Resend error {}: {}", status, body),
        ));
    }

    // Redirect back to index with success message
    Ok(Redirect::to("/?subscribed=1"))
}

/// Health check endpoint - verifies DB is accessible
async fn health(State(state): State<Arc<AppState>>) -> Result<&'static str, (StatusCode, String)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, format!("DB error: {e}")))?;

    conn.query_row("SELECT 1", [], |_| Ok(())).map_err(|e| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            format!("DB query failed: {e}"),
        )
    })?;

    Ok("ok")
}

#[derive(Deserialize, Default)]
struct StatsQuery {
    days: Option<u32>,
}

#[derive(Clone)]
struct SourceHealth {
    source_id: String,
    total_fetches: i64,
    successes: i64,
    success_rate_pct: f64,
}

#[derive(Clone)]
struct SourceUsage {
    source_id: String,
    tier: String,
    count: i64,
}

#[derive(Clone)]
struct DigestRun {
    run_at: String,
    articles_fetched: i64,
    articles_emailed: i64,
}

struct StatsData {
    period_days: u32,
    source_health: Vec<SourceHealth>,
    source_usage: Vec<SourceUsage>,
    recent_runs: Vec<DigestRun>,
}

/// Fetch stats data from database
fn fetch_stats_data(db_path: &str, days: u32) -> Result<StatsData, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Source health: success rate per source over last N days
    let source_health: Vec<SourceHealth> = {
        let mut stmt = conn
            .prepare(
                "SELECT source_id,
                        COUNT(*) as total,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
                 FROM source_health
                 WHERE recorded_at >= datetime('now', '-' || ?1 || ' days')
                 GROUP BY source_id
                 ORDER BY source_id",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| {
            let source_id: String = row.get(0)?;
            let total: i64 = row.get(1)?;
            let successes: i64 = row.get(2)?;
            let rate = if total > 0 {
                (successes as f64 / total as f64 * 100.0).round()
            } else {
                0.0
            };
            Ok(SourceHealth {
                source_id,
                total_fetches: total,
                successes,
                success_rate_pct: rate,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| r.ok())
        .collect()
    };

    // Source usage: how often each source appears in digests, by tier
    let source_usage: Vec<SourceUsage> = {
        let mut stmt = conn
            .prepare(
                "SELECT source_id, tier, COUNT(*) as count
                 FROM shown_narratives
                 WHERE source_id IS NOT NULL
                   AND shown_at >= datetime('now', '-' || ?1 || ' days')
                 GROUP BY source_id, tier
                 ORDER BY count DESC",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| {
            Ok(SourceUsage {
                source_id: row.get(0)?,
                tier: row.get(1)?,
                count: row.get(2)?,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| r.ok())
        .collect()
    };

    // Recent runs: last 10 digest runs
    let recent_runs: Vec<DigestRun> = {
        let mut stmt = conn
            .prepare(
                "SELECT run_at, articles_fetched, articles_emailed
                 FROM digest_runs
                 ORDER BY run_at DESC
                 LIMIT 10",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([], |row| {
            Ok(DigestRun {
                run_at: row.get(0)?,
                articles_fetched: row.get(1)?,
                articles_emailed: row.get(2)?,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| r.ok())
        .collect()
    };

    Ok(StatsData {
        period_days: days,
        source_health,
        source_usage,
        recent_runs,
    })
}

/// Stats JSON endpoint
async fn stats_json(
    State(state): State<Arc<AppState>>,
    Query(query): Query<StatsQuery>,
) -> Result<axum::Json<serde_json::Value>, (StatusCode, String)> {
    let days = query.days.unwrap_or(30);
    let data = fetch_stats_data(&state.db_path, days)?;

    let source_health: Vec<serde_json::Value> = data
        .source_health
        .iter()
        .map(|h| {
            serde_json::json!({
                "source_id": h.source_id,
                "total_fetches": h.total_fetches,
                "successes": h.successes,
                "success_rate_pct": h.success_rate_pct
            })
        })
        .collect();

    let source_usage: Vec<serde_json::Value> = data
        .source_usage
        .iter()
        .map(|u| {
            serde_json::json!({
                "source_id": u.source_id,
                "tier": u.tier,
                "count": u.count
            })
        })
        .collect();

    let recent_runs: Vec<serde_json::Value> = data
        .recent_runs
        .iter()
        .map(|r| {
            serde_json::json!({
                "run_at": r.run_at,
                "articles_fetched": r.articles_fetched,
                "articles_emailed": r.articles_emailed
            })
        })
        .collect();

    Ok(axum::Json(serde_json::json!({
        "period_days": data.period_days,
        "source_health": source_health,
        "source_usage": source_usage,
        "recent_runs": recent_runs
    })))
}

/// Stats HTML dashboard
async fn stats_html(
    State(state): State<Arc<AppState>>,
    Query(query): Query<StatsQuery>,
) -> Result<Html<String>, (StatusCode, String)> {
    let days = query.days.unwrap_or(30);
    let data = fetch_stats_data(&state.db_path, days)?;
    let name = &state.digest_name;
    let css_link = state
        .css_url
        .as_ref()
        .map(|url| format!(r#"<link rel="stylesheet" href="{url}">"#))
        .unwrap_or_default();

    // Build source health table rows
    let health_rows: String = if data.source_health.is_empty() {
        r#"<tr><td colspan="4" class="empty">No data yet</td></tr>"#.to_string()
    } else {
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
    };

    // Build source usage table rows (aggregate by source)
    // Tiers: must_know, should_know, signal/quick_signal/below_fold (all count as "other")
    let mut usage_by_source: std::collections::HashMap<String, (i64, i64, i64)> =
        std::collections::HashMap::new();
    for u in &data.source_usage {
        let entry = usage_by_source.entry(u.source_id.clone()).or_default();
        match u.tier.as_str() {
            "must_know" => entry.0 += u.count,
            "should_know" => entry.1 += u.count,
            // All signal-like tiers (signal, quick_signal, below_fold) grouped together
            _ => entry.2 += u.count,
        }
    }
    let mut usage_sorted: Vec<_> = usage_by_source.into_iter().collect();
    usage_sorted.sort_by(|a, b| {
        let total_a = a.1.0 + a.1.1 + a.1.2;
        let total_b = b.1.0 + b.1.1 + b.1.2;
        total_b.cmp(&total_a)
    });

    let usage_rows: String = if usage_sorted.is_empty() {
        r#"<tr><td colspan="5" class="empty">No data yet</td></tr>"#.to_string()
    } else {
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
    };

    // Build recent runs table rows
    let runs_rows: String = if data.recent_runs.is_empty() {
        r#"<tr><td colspan="3" class="empty">No runs yet</td></tr>"#.to_string()
    } else {
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
    };

    let html = format!(
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
    td.empty {{
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
  </div>
</body>
</html>"##,
        if days == 7 { " class=\"active\"" } else { "" },
        if days == 30 { " class=\"active\"" } else { "" },
        if days == 90 { " class=\"active\"" } else { "" },
    );

    Ok(Html(html))
}

/// Serve digest HTML by date (YYYY-MM-DD)
async fn get_digest(
    Path(date): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, String)> {
    // Validate date format: exactly YYYY-MM-DD
    if !is_valid_date(&date) {
        return Err((StatusCode::BAD_REQUEST, "Invalid date format".into()));
    }

    // Open database read-only
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Query for digest HTML
    let html: String = conn
        .query_row("SELECT html FROM digests WHERE date = ?1", [&date], |row| {
            row.get(0)
        })
        .map_err(|_| (StatusCode::NOT_FOUND, format!("No digest for {date}")))?;

    // Inject navigation header CSS and HTML when viewing in browser
    let nav_css = r#"<style>
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

    let nav_html = r#"<nav class="digest-nav">
    <a href="/">← All digests</a>
    <a href="/">Subscribe</a>
</nav>"#;

    // Insert CSS before </head> and nav after <body>
    let html = html.replacen("</head>", &format!("{}</head>", nav_css), 1);
    let html = html.replacen("<body>", &format!("<body>{}", nav_html), 1);

    Ok(Html(html))
}

/// Format date from YYYY-MM-DD to "Friday, January 17"
fn format_date(date_str: &str) -> String {
    let parts: Vec<&str> = date_str.split('-').collect();
    if parts.len() != 3 {
        return date_str.to_string();
    }

    let year: i32 = parts[0].parse().unwrap_or(2026);
    let month: u32 = parts[1].parse().unwrap_or(1);
    let day: u32 = parts[2].parse().unwrap_or(1);

    let months = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ];
    let days = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ];

    // Zeller's congruence for day of week
    let (y, m) = if month < 3 {
        (year - 1, month + 12)
    } else {
        (year, month)
    };
    let q = day as i32;
    let k = y % 100;
    let j = y / 100;
    let h = (q + (13 * (m as i32 + 1)) / 5 + k + k / 4 + j / 4 - 2 * j) % 7;
    let dow = ((h + 6) % 7) as usize;

    format!("{}, {} {}", days[dow], months[month as usize], day)
}

/// Validate date is exactly YYYY-MM-DD format with valid numbers
fn is_valid_date(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    if parts.len() != 3 {
        return false;
    }
    // Year: 4 digits, Month: 01-12, Day: 01-31
    let year_ok = parts[0].len() == 4 && parts[0].chars().all(|c| c.is_ascii_digit());
    let month_ok = parts[1].parse::<u8>().is_ok_and(|m| (1..=12).contains(&m));
    let day_ok = parts[2].parse::<u8>().is_ok_and(|d| (1..=31).contains(&d));
    year_ok && month_ok && day_ok
}

#[tokio::main]
async fn main() {
    let db_path = std::env::var("DATABASE_PATH").unwrap_or_else(|_| "/data/digest.db".into());

    // Validate database path is within expected directories
    if !db_path.starts_with("/data/")
        && !db_path.starts_with("/app/data/")
        && !db_path.starts_with("./data/")
    {
        eprintln!("DATABASE_PATH must be within /data/, /app/data/, or ./data/");
        std::process::exit(1);
    }

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let addr = format!("0.0.0.0:{port}");

    // Verify database exists and has digests table
    if let Err(e) = verify_database(&db_path) {
        eprintln!("Database error: {e}");
        std::process::exit(1);
    }

    let digest_name = std::env::var("DIGEST_NAME").unwrap_or_else(|_| "News Digest".into());
    let css_url = std::env::var("CSS_URL").ok();
    let homepage_url = std::env::var("HOMEPAGE_URL").ok();
    let source_url = std::env::var("SOURCE_URL").ok();
    let resend_api_key = std::env::var("RESEND_API_KEY").ok();
    let resend_audience_id = std::env::var("RESEND_AUDIENCE_ID").ok();
    let http_client = Client::new();

    let state = Arc::new(AppState {
        db_path,
        digest_name,
        css_url,
        homepage_url,
        source_url,
        resend_api_key,
        resend_audience_id,
        http_client,
    });

    let app = Router::new()
        .route("/", get(index))
        .route("/subscribe", post(subscribe))
        .route("/health", get(health))
        .route("/stats", get(stats_html))
        .route("/stats.json", get(stats_json))
        .route("/{date}", get(get_digest))
        .with_state(state);

    println!("digest-server listening on {addr}");

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

fn verify_database(path: &str) -> Result<(), String> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("Cannot open database: {e}"))?;

    // Check that digests table exists
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='digests'",
        [],
        |_| Ok(()),
    )
    .map_err(|_| "Table 'digests' not found".to_string())?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    mod is_valid_date {
        use super::*;

        #[test]
        fn valid_date() {
            assert!(is_valid_date("2026-01-24"));
            assert!(is_valid_date("2025-12-31"));
            assert!(is_valid_date("2000-01-01"));
        }

        #[test]
        fn invalid_month() {
            assert!(!is_valid_date("2026-00-15"));
            assert!(!is_valid_date("2026-13-15"));
        }

        #[test]
        fn invalid_day() {
            assert!(!is_valid_date("2026-01-00"));
            assert!(!is_valid_date("2026-01-32"));
        }

        #[test]
        fn wrong_format() {
            assert!(!is_valid_date("01-24-2026")); // US format
            assert!(!is_valid_date("2026/01/24")); // slashes
            assert!(!is_valid_date("20260124")); // no separators
        }

        #[test]
        fn lenient_on_leading_zeros() {
            // Parser accepts single digits (lenient but safe)
            assert!(is_valid_date("2026-1-24"));
            assert!(is_valid_date("2026-01-4"));
        }

        #[test]
        fn malformed_input() {
            assert!(!is_valid_date(""));
            assert!(!is_valid_date("not-a-date"));
            assert!(!is_valid_date("2026-01"));
            assert!(!is_valid_date("2026-01-24-extra"));
        }

        #[test]
        fn path_traversal_rejected() {
            assert!(!is_valid_date("../etc/passwd"));
            assert!(!is_valid_date("2026-01-24; DROP TABLE"));
        }
    }

    mod format_date {
        use super::*;

        #[test]
        fn formats_correctly() {
            assert_eq!(format_date("2026-01-24"), "Saturday, January 24");
            assert_eq!(format_date("2025-12-25"), "Thursday, December 25");
            assert_eq!(format_date("2026-07-04"), "Saturday, July 4");
        }

        #[test]
        fn handles_different_days_of_week() {
            // 2026-01-19 is Monday, 2026-01-25 is Sunday
            assert_eq!(format_date("2026-01-19"), "Monday, January 19");
            assert_eq!(format_date("2026-01-20"), "Tuesday, January 20");
            assert_eq!(format_date("2026-01-21"), "Wednesday, January 21");
            assert_eq!(format_date("2026-01-22"), "Thursday, January 22");
            assert_eq!(format_date("2026-01-23"), "Friday, January 23");
            assert_eq!(format_date("2026-01-24"), "Saturday, January 24");
            assert_eq!(format_date("2026-01-25"), "Sunday, January 25");
        }

        #[test]
        fn handles_all_months() {
            assert!(format_date("2026-01-15").contains("January"));
            assert!(format_date("2026-02-15").contains("February"));
            assert!(format_date("2026-03-15").contains("March"));
            assert!(format_date("2026-04-15").contains("April"));
            assert!(format_date("2026-05-15").contains("May"));
            assert!(format_date("2026-06-15").contains("June"));
            assert!(format_date("2026-07-15").contains("July"));
            assert!(format_date("2026-08-15").contains("August"));
            assert!(format_date("2026-09-15").contains("September"));
            assert!(format_date("2026-10-15").contains("October"));
            assert!(format_date("2026-11-15").contains("November"));
            assert!(format_date("2026-12-15").contains("December"));
        }

        #[test]
        fn invalid_input_returns_original() {
            assert_eq!(format_date("not-valid"), "not-valid");
            assert_eq!(format_date(""), "");
        }
    }
}
