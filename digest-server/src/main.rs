use axum::{
    Router,
    extract::{Path, State},
    http::StatusCode,
    response::Html,
    routing::get,
};
use rusqlite::{Connection, OpenFlags};
use std::sync::Arc;

struct AppState {
    db_path: String,
}

/// Health check endpoint - verifies DB is accessible
async fn health(State(state): State<Arc<AppState>>) -> Result<&'static str, (StatusCode, String)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, format!("DB error: {e}")))?;

    conn.query_row("SELECT 1", [], |_| Ok(()))
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, format!("DB query failed: {e}")))?;

    Ok("ok")
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
        .query_row(
            "SELECT html FROM digests WHERE date = ?1",
            [&date],
            |row| row.get(0),
        )
        .map_err(|_| (StatusCode::NOT_FOUND, format!("No digest for {date}")))?;

    Ok(Html(html))
}

/// Validate date is exactly YYYY-MM-DD format with valid numbers
fn is_valid_date(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    if parts.len() != 3 {
        return false;
    }
    // Year: 4 digits, Month: 01-12, Day: 01-31
    let year_ok = parts[0].len() == 4 && parts[0].chars().all(|c| c.is_ascii_digit());
    let month_ok = parts[1].parse::<u8>().map_or(false, |m| (1..=12).contains(&m));
    let day_ok = parts[2].parse::<u8>().map_or(false, |d| (1..=31).contains(&d));
    year_ok && month_ok && day_ok
}

#[tokio::main]
async fn main() {
    let db_path = std::env::var("DATABASE_PATH").unwrap_or_else(|_| "/data/digest.db".into());

    // Validate database path is within expected directories
    if !db_path.starts_with("/data/") && !db_path.starts_with("/app/data/") && !db_path.starts_with("./data/") {
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

    let state = Arc::new(AppState { db_path });

    let app = Router::new()
        .route("/health", get(health))
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
