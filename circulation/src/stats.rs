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
    pub articles_kept: i64,
    pub articles_emailed: i64,
    pub api_cost_usd: Option<f64>,
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
                "SELECT dr.run_at, dr.articles_kept, dr.articles_emailed,
                        SUM(ru.api_cost_usd) as total_cost
                 FROM digest_runs dr
                 LEFT JOIN run_usage ru ON ru.run_id = dr.id
                 WHERE dr.completed_at IS NOT NULL
                 GROUP BY dr.id
                 ORDER BY dr.run_at DESC
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
                articles_kept: row.get(1)?,
                articles_emailed: row.get(2)?,
                api_cost_usd: row.get(3)?,
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
                "articles_kept": r.articles_kept,
                "articles_emailed": r.articles_emailed,
                "api_cost_usd": r.api_cost_usd
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
    let html = render_stats(name, days, &data);
    Ok(Html(html))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static COUNTER: AtomicU32 = AtomicU32::new(0);

    /// Throwaway on-disk sqlite with the stats-relevant schema (mirrors the archive/thread test
    /// fixtures). Columns match the production tables the queries touch; caller seeds rows via
    /// `inserts` (an SQL batch, using `datetime('now', ...)` to place rows in/out of the window).
    fn seed_db(inserts: &str) -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir()
            .join(format!("stats_test_{}_{n}.db", std::process::id()))
            .to_string_lossy()
            .into_owned();
        let _ = std::fs::remove_file(&path);
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE source_health (source_id TEXT NOT NULL, success INTEGER NOT NULL, recorded_at DATETIME);
             CREATE TABLE shown_narratives (headline TEXT, tier TEXT, source_id TEXT, shown_at DATETIME);
             CREATE TABLE digest_runs (id INTEGER PRIMARY KEY, run_at DATETIME, articles_kept INTEGER, articles_emailed INTEGER, completed_at DATETIME);
             CREATE TABLE run_usage (run_id INTEGER, api_cost_usd REAL);
             CREATE TABLE dedup_log (similarity REAL, logged_at DATETIME);",
        )
        .unwrap();
        if !inserts.is_empty() {
            conn.execute_batch(inserts).unwrap();
        }
        drop(conn);
        path
    }

    #[test]
    fn happy_path_aggregates_across_the_window() {
        // In-window rows use datetime('now'); the '-40 days' rows must be excluded by the 30-day window.
        let path = seed_db(
            "INSERT INTO source_health (source_id, success, recorded_at) VALUES
                ('bbc', 1, datetime('now')),
                ('bbc', 1, datetime('now')),
                ('bbc', 0, datetime('now')),
                ('bbc', 0, datetime('now','-40 days')),   -- out of window: must not count
                ('reuters', 1, datetime('now')),
                ('dead_feed', 0, datetime('now'));         -- fetched but never selected

             INSERT INTO shown_narratives (headline, tier, source_id, shown_at) VALUES
                ('h1', 'must_know', 'bbc', datetime('now')),
                ('h2', 'must_know', 'bbc', datetime('now')),
                ('h3', 'should_know', 'reuters', datetime('now')),
                ('h4', 'must_know', NULL, datetime('now')),          -- null source_id: excluded from usage
                ('h5', 'must_know', 'bbc', datetime('now','-40 days')); -- out of window: excluded

             INSERT INTO digest_runs (id, run_at, articles_kept, articles_emailed, completed_at) VALUES
                (1, '2026-07-01T10:00:00', 10, 8, '2026-07-01T10:05:00'),
                (2, '2026-07-02T10:00:00', 5, 5, '2026-07-02T10:05:00'),
                (3, '2026-07-03T10:00:00', 7, 0, NULL);              -- not completed: excluded

             INSERT INTO run_usage (run_id, api_cost_usd) VALUES
                (1, 1.0),
                (1, 0.5);                                            -- run 2 has no usage rows

             INSERT INTO dedup_log (similarity, logged_at) VALUES
                (0.4, datetime('now')),
                (0.6, datetime('now')),
                (0.8, datetime('now')),
                (0.99, datetime('now','-40 days'));                 -- out of window: excluded",
        );

        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert_eq!(data.period_days, 30);

        // Source health: grouped per source, ordered by source_id; rate is round(successes/total*100).
        assert_eq!(data.source_health.len(), 3);
        let bbc = &data.source_health[0];
        assert_eq!(bbc.source_id, "bbc");
        assert_eq!(bbc.total_fetches, 3); // the -40d row is excluded
        assert_eq!(bbc.successes, 2);
        assert_eq!(bbc.success_rate_pct, 67.0); // round(2/3*100)
        let dead = &data.source_health[1];
        assert_eq!(dead.source_id, "dead_feed");
        assert_eq!(dead.success_rate_pct, 0.0);
        assert_eq!(data.source_health[2].source_id, "reuters");
        assert_eq!(data.source_health[2].success_rate_pct, 100.0);

        // Source usage: non-null source_id in window, grouped by (source,tier), ordered by count DESC.
        assert_eq!(data.source_usage.len(), 2);
        assert_eq!(data.source_usage[0].source_id, "bbc");
        assert_eq!(data.source_usage[0].tier, "must_know");
        assert_eq!(data.source_usage[0].count, 2);
        assert_eq!(data.source_usage[1].source_id, "reuters");
        assert_eq!(data.source_usage[1].count, 1);

        // Recent runs: completed only, newest first; api_cost is SUM(run_usage) or None when absent.
        assert_eq!(data.recent_runs.len(), 2); // run 3 excluded (completed_at NULL)
        assert_eq!(data.recent_runs[0].run_at, "2026-07-02T10:00:00");
        assert_eq!(data.recent_runs[0].articles_kept, 5);
        assert_eq!(data.recent_runs[0].api_cost_usd, None); // no run_usage rows -> SUM is NULL
        assert_eq!(data.recent_runs[1].run_at, "2026-07-01T10:00:00");
        assert_eq!(data.recent_runs[1].articles_emailed, 8);
        assert_eq!(data.recent_runs[1].api_cost_usd, Some(1.5)); // 1.0 + 0.5

        // Dedup: aggregates over in-window rows only.
        let d = data.dedup_stats.expect("dedup stats present");
        assert_eq!(d.filtered_count, 3);
        assert!((d.avg_similarity - 0.6).abs() < 1e-9);
        assert_eq!(d.min_similarity, 0.4);
        assert_eq!(d.max_similarity, 0.8);

        // Never-selected: in source_health window but absent from shown_narratives window.
        assert_eq!(data.never_selected, vec!["dead_feed".to_string()]);
    }

    #[test]
    fn empty_db_yields_zeroes_and_no_dedup() {
        let path = seed_db("");
        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert!(data.source_health.is_empty());
        assert!(data.source_usage.is_empty());
        assert!(data.recent_runs.is_empty());
        assert!(data.never_selected.is_empty());
        assert!(data.dedup_stats.is_none()); // COUNT(*)=0 -> None (never reads the NULL AVG/MIN/MAX)
    }

    #[test]
    fn dedup_out_of_window_returns_none_not_panic() {
        // Rows exist but all fall outside the window: COUNT(*) is 0, so AVG/MIN/MAX come back NULL.
        // The null-guard must return None rather than try to read NULL into f64 (which would error).
        let path = seed_db(
            "INSERT INTO dedup_log (similarity, logged_at) VALUES
                (0.5, datetime('now','-40 days')),
                (0.7, datetime('now','-99 days'));",
        );
        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert!(data.dedup_stats.is_none());
    }
}
