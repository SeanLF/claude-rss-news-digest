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
    let meta_links = match (homepage_link, source_link) {
        (Some(h), Some(s)) => format!(r#"<p class="meta-links">{h} · {s}</p>"#),
        (Some(h), None) => format!(r#"<p class="meta-links">{h}</p>"#),
        (None, Some(s)) => format!(r#"<p class="meta-links">{s}</p>"#),
        (None, None) => String::new(),
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
