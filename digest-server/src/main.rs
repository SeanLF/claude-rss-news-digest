mod handlers;
mod stats;
mod templates;
mod util;

use axum::Router;
use axum::routing::{get, post};
use reqwest::Client;
use rusqlite::{Connection, OpenFlags};
use std::sync::Arc;
use tower_http::trace::TraceLayer;

pub struct AppState {
    pub db_path: String,
    pub digest_name: String,
    pub css_url: Option<String>,
    pub homepage_url: Option<String>,
    pub source_url: Option<String>,
    pub resend_api_key: Option<String>,
    pub resend_audience_id: Option<String>,
    pub http_client: Client,
}

#[tokio::main]
async fn main() {
    // Initialize tracing subscriber (respects RUST_LOG env var, defaults to info)
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let db_path = std::env::var("DATABASE_PATH").unwrap_or_else(|_| "/data/digest.db".into());

    // Validate database path is within expected directories
    if !db_path.starts_with("/data/")
        && !db_path.starts_with("/app/data/")
        && !db_path.starts_with("./data/")
    {
        tracing::error!("DATABASE_PATH must be within /data/, /app/data/, or ./data/");
        std::process::exit(1);
    }

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let addr = format!("0.0.0.0:{port}");

    // Verify database exists and has digests table
    if let Err(e) = verify_database(&db_path) {
        tracing::error!("Database error: {}", e);
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
        .route("/", get(handlers::index))
        .route("/subscribe", post(handlers::subscribe))
        .route("/health", get(handlers::health))
        .route("/stats", get(stats::stats_html))
        .route("/stats.json", get(stats::stats_json))
        .route("/{date}", get(handlers::get_digest))
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    tracing::info!("digest-server listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

/// Required tables for the server to function
const REQUIRED_TABLES: &[&str] = &[
    "digests",
    "digest_runs",
    "source_health",
    "shown_narratives",
    "dedup_log",
];

/// Check database health - returns list of missing tables (empty if healthy).
/// Returns `["database"]` if the database file cannot be opened.
pub fn check_database_health(path: &str) -> Vec<String> {
    let conn = match Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY) {
        Ok(c) => c,
        Err(_) => return vec!["database".to_string()],
    };

    REQUIRED_TABLES
        .iter()
        .filter(|table| {
            conn.query_row(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
                [table],
                |row| row.get::<_, i32>(0),
            )
            .is_err()
        })
        .map(|t| t.to_string())
        .collect()
}

fn verify_database(path: &str) -> Result<(), String> {
    let missing = check_database_health(path);

    if missing.contains(&"database".to_string()) {
        return Err(format!("Cannot open database: {path}"));
    }

    if !missing.is_empty() {
        return Err(format!(
            "Missing tables: {}. Run: bin/migrate",
            missing.join(", ")
        ));
    }

    Ok(())
}
