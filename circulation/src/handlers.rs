use axum::{
    Form, Json,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Redirect},
};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::AppState;
use crate::check_database_health;
use crate::templates::{DIGEST_NAV_CSS, DIGEST_NAV_HTML, render_index};
use crate::util::{escape_html, format_date, is_valid_date, log_row_error};

#[derive(Deserialize)]
pub struct SubscribeForm {
    pub email: String,
}

#[derive(Deserialize, Default)]
pub struct IndexQuery {
    pub subscribed: Option<String>,
}

#[derive(Serialize)]
struct ResendContact {
    email: String,
}

/// Index page - lists recent digests
pub async fn index(
    State(state): State<Arc<AppState>>,
    Query(query): Query<IndexQuery>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?;

    // Get list of available digests (most recent first)
    let mut stmt = conn
        .prepare("SELECT date, COALESCE(preheader, '') FROM digests ORDER BY date DESC LIMIT 30")
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?;

    let digests: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?
        .filter_map(|r| log_row_error(r, "digests"))
        .collect();

    let links: String = digests
        .iter()
        .map(|(d, preheader)| {
            let formatted = format_date(d);
            let preheader_html = if preheader.is_empty() {
                String::new()
            } else {
                let escaped = escape_html(preheader);
                format!(r#"<span class="preheader-text">{escaped}</span>"#)
            };
            format!(
                r#"<li><a href="/{d}"><span class="link-content"><span class="date-text">{formatted}</span>{preheader_html}</span><span class="arrow">→</span></a></li>"#
            )
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
    let source_link = state
        .source_url
        .as_ref()
        .map(|url| format!(r#"<a href="{url}" class="meta-link">GitHub</a>"#));
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

    let html = render_index(
        name,
        &css_link,
        &meta_links,
        success_msg,
        subscribe_form,
        &links,
    );
    Ok(Html(html))
}

/// Subscribe handler - adds email to Resend audience
pub async fn subscribe(
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

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    missing_tables: Option<Vec<String>>,
}

/// Health check endpoint - verifies DB is accessible and schema is complete
pub async fn health(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let missing = check_database_health(&state.db_path);

    if missing.is_empty() {
        (
            StatusCode::OK,
            Json(HealthResponse {
                status: "healthy",
                missing_tables: None,
            }),
        )
    } else {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(HealthResponse {
                status: "degraded",
                missing_tables: Some(missing),
            }),
        )
    }
}

/// Serve digest HTML by date (YYYY-MM-DD)
pub async fn get_digest(
    Path(date): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    // Validate date format: exactly YYYY-MM-DD
    if !is_valid_date(&date) {
        return Err((StatusCode::BAD_REQUEST, "Invalid date format"));
    }

    // Open database read-only
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable"))?;

    // Query for digest HTML
    let html: String = conn
        .query_row("SELECT html FROM digests WHERE date = ?1", [&date], |row| {
            row.get(0)
        })
        .map_err(|_| (StatusCode::NOT_FOUND, "Digest not found"))?;

    // Insert CSS before </head> and nav after <body>
    let html = html.replacen("</head>", &format!("{DIGEST_NAV_CSS}</head>"), 1);
    let html = html.replacen("<body>", &format!("<body>{DIGEST_NAV_HTML}"), 1);

    Ok(Html(html))
}
