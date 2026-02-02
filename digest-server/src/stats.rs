use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Html,
};
use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;
use std::sync::Arc;

use crate::AppState;

#[derive(Deserialize, Default)]
pub struct StatsQuery {
    pub days: Option<u32>,
}

#[derive(Clone)]
pub struct SourceHealth {
    pub source_id: String,
    pub total_fetches: i64,
    pub successes: i64,
    pub success_rate_pct: f64,
}

#[derive(Clone)]
pub struct SourceUsage {
    pub source_id: String,
    pub tier: String,
    pub count: i64,
}

#[derive(Clone)]
pub struct DigestRun {
    pub run_at: String,
    pub articles_fetched: i64,
    pub articles_emailed: i64,
}

#[derive(Clone)]
pub struct DedupStats {
    pub filtered_count: i64,
    pub avg_similarity: f64,
    pub min_similarity: f64,
    pub max_similarity: f64,
}

pub struct StatsData {
    pub period_days: u32,
    pub source_health: Vec<SourceHealth>,
    pub source_usage: Vec<SourceUsage>,
    pub recent_runs: Vec<DigestRun>,
    pub dedup_stats: Option<DedupStats>,
    pub never_selected: Vec<String>,
}

/// Fetch stats data from database
pub fn fetch_stats_data(db_path: &str, days: u32) -> Result<StatsData, (StatusCode, String)> {
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

    // Dedup stats: how effective is the TF-IDF filter
    let dedup_stats: Option<DedupStats> = conn
        .query_row(
            "SELECT COUNT(*) as filtered,
                    AVG(similarity) as avg_sim,
                    MIN(similarity) as min_sim,
                    MAX(similarity) as max_sim
             FROM dedup_log
             WHERE logged_at >= datetime('now', '-' || ?1 || ' days')",
            [days],
            |row| {
                let count: i64 = row.get(0)?;
                if count == 0 {
                    Ok(None)
                } else {
                    Ok(Some(DedupStats {
                        filtered_count: count,
                        avg_similarity: row.get(1)?,
                        min_similarity: row.get(2)?,
                        max_similarity: row.get(3)?,
                    }))
                }
            },
        )
        .unwrap_or(None);

    // Never-selected sources: in source_health but not in shown_narratives
    let never_selected: Vec<String> = {
        let mut stmt = conn
            .prepare(
                "SELECT DISTINCT h.source_id
                 FROM source_health h
                 WHERE h.recorded_at >= datetime('now', '-' || ?1 || ' days')
                   AND h.source_id NOT IN (
                       SELECT DISTINCT source_id
                       FROM shown_narratives
                       WHERE source_id IS NOT NULL
                         AND shown_at >= datetime('now', '-' || ?1 || ' days')
                   )
                 ORDER BY h.source_id",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| row.get(0))
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
        dedup_stats,
        never_selected,
    })
}

/// Stats JSON endpoint
pub async fn stats_json(
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

    let dedup_stats = data.dedup_stats.as_ref().map(|d| {
        serde_json::json!({
            "filtered_count": d.filtered_count,
            "avg_similarity": d.avg_similarity,
            "min_similarity": d.min_similarity,
            "max_similarity": d.max_similarity
        })
    });

    Ok(axum::Json(serde_json::json!({
        "period_days": data.period_days,
        "source_health": source_health,
        "source_usage": source_usage,
        "recent_runs": recent_runs,
        "dedup_stats": dedup_stats,
        "never_selected": data.never_selected
    })))
}

/// Stats HTML dashboard
pub async fn stats_html(
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

    // Build dedup stats row
    let dedup_row: String = match &data.dedup_stats {
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
    };

    // Build never-selected list
    let never_selected_content: String = if data.never_selected.is_empty() {
        r#"<p class="empty">All sources have been selected at least once</p>"#.to_string()
    } else {
        format!(
            r#"<p class="source-list">{}</p>"#,
            data.never_selected.join(", ")
        )
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
    );

    Ok(Html(html))
}
