use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Html,
};
use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;
use std::sync::Arc;

use crate::AppState;
use crate::templates::render_stats;
use crate::util::log_row_error;

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
        .filter_map(|r| log_row_error(r, "source_health"))
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
        .filter_map(|r| log_row_error(r, "shown_narratives"))
        .collect()
    };

    // Recent runs: last 10 digest runs
    let recent_runs: Vec<DigestRun> = {
        let mut stmt = conn
            .prepare(
                "SELECT run_at, articles_fetched, articles_emailed
                 FROM digest_runs
                 WHERE completed_at IS NOT NULL
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
        .filter_map(|r| log_row_error(r, "digest_runs"))
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
            .filter_map(|r| log_row_error(r, "source_health"))
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

    let html = render_stats(name, &css_link, days, &data);
    Ok(Html(html))
}
