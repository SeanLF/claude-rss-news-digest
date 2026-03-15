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
use crate::templates::{
    DIGEST_NAV_CSS, DIGEST_NAV_HTML, FAVICON_SVG, IndexParams, digest_og_tags, render_index,
    web_footer_html,
};
use crate::util::{
    escape_html, format_date, format_month_year, is_valid_date, log_row_error, year_month,
};

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
        .prepare("SELECT date, COALESCE(preheader, '') FROM digests ORDER BY date DESC")
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?;

    let digests: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?
        .filter_map(|r| log_row_error(r, "digests"))
        .collect();

    // Build digest list HTML with collapsible month groups
    let mut links = String::new();
    let mut current_month = String::new();
    let mut is_first_month = true;
    for (d, preheader) in &digests {
        let ym = year_month(d).to_string();
        if ym != current_month {
            // Close previous month group
            if !current_month.is_empty() {
                links.push_str("</ul></details>");
            }
            let month_label = format_month_year(&ym);
            let open_attr = if is_first_month { " open" } else { "" };
            links.push_str(&format!(
                r#"<details{open_attr}><summary class="month-heading">{month_label}</summary><ul>"#
            ));
            current_month = ym;
            is_first_month = false;
        }
        let formatted = format_date(d);
        let preheader_html = if preheader.is_empty() {
            String::new()
        } else {
            let escaped = escape_html(preheader);
            format!(r#"<span class="preheader-text">{escaped}</span>"#)
        };
        links.push_str(&format!(
            r#"<li><a href="/{d}"><span class="date-text">{formatted}</span>{preheader_html}</a></li>"#
        ));
    }
    // Close last month group
    if !current_month.is_empty() {
        links.push_str("</ul></details>");
    }

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
    let subscribe_teaser = if subscriptions_enabled {
        digests.first().map_or(String::new(), |(latest_date, _)| {
            format!(
                r#"<p class="subscribe-teaser"><a href="/{latest_date}">Open the latest digest</a> to see what you'd receive daily.</p>"#
            )
        })
    } else {
        String::new()
    };
    let homepage_link = state.homepage_url.as_ref().map(|url| {
        let display = url
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        format!(r#"<a href="{url}" class="meta-link">🌐 {display}</a>"#)
    });
    let source_link = state
        .source_url
        .as_ref()
        .map(|url| format!(r#"<a href="{url}" class="meta-link">📦 GitHub</a>"#));
    let stats_link = r#"<a href="/stats" class="meta-link">📊 Stats</a>"#;
    let meta_links = match (homepage_link, source_link) {
        (Some(h), Some(s)) => format!(r#"<p class="meta-links">{h} · {s} · {stats_link}</p>"#),
        (Some(h), None) => format!(r#"<p class="meta-links">{h} · {stats_link}</p>"#),
        (None, Some(s)) => format!(r#"<p class="meta-links">{s} · {stats_link}</p>"#),
        (None, None) => format!(r#"<p class="meta-links">{stats_link}</p>"#),
    };
    let og_description = "Daily briefing on geopolitics, tech, and privacy. All sides. No fluff.";
    let canonical_url = state
        .digest_domain
        .as_ref()
        .map(|d| format!("https://{d}"))
        .unwrap_or_default();

    let html = render_index(&IndexParams {
        name,
        meta_links: &meta_links,
        success_msg,
        subscribe_form,
        subscribe_teaser: &subscribe_teaser,
        digest_links: &links,
        og_description,
        canonical_url: &canonical_url,
    });
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
            tracing::error!("Resend request failed: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                "Subscription failed, please try again".into(),
            )
        })?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        tracing::error!(status = %status, body, "Resend API error");
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            "Subscription failed, please try again".into(),
        ));
    }
    Ok(Redirect::to("/?subscribed=1"))
}

const FAVICON_SVG_RAW: &[u8] = b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='#c45a3b'/><line x1='8' y1='10' x2='24' y2='10' stroke='white' stroke-width='2.5' stroke-linecap='round'/><line x1='8' y1='16' x2='20' y2='16' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.7'/><line x1='8' y1='22' x2='16' y2='22' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.4'/></svg>";

/// Serve robots.txt
pub async fn robots_txt() -> impl IntoResponse {
    (
        StatusCode::OK,
        [("content-type", "text/plain; charset=utf-8")],
        "User-agent: *\nAllow: /\n",
    )
}

const APPLE_TOUCH_ICON_PNG: &[u8] = include_bytes!("../apple-touch-icon.png");

/// Serve apple-touch-icon as PNG for iMessage/WhatsApp/social previews
pub async fn apple_touch_icon() -> impl IntoResponse {
    (
        StatusCode::OK,
        [
            ("content-type", "image/png"),
            ("cache-control", "public, max-age=86400"),
        ],
        APPLE_TOUCH_ICON_PNG,
    )
}

/// Serve favicon as SVG
pub async fn favicon() -> impl IntoResponse {
    (
        StatusCode::OK,
        [
            ("content-type", "image/svg+xml"),
            ("cache-control", "public, max-age=86400"),
        ],
        FAVICON_SVG_RAW,
    )
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

/// Replace `needle` with `replacement` in `html`, warning if the needle is missing.
fn inject(html: &str, needle: &str, replacement: &str, date: &str) -> String {
    if html.contains(needle) {
        html.replacen(needle, replacement, 1)
    } else {
        tracing::warn!(
            date,
            needle,
            "web injection missed -- stored HTML may have drifted"
        );
        html.to_string()
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

    // Query for digest HTML and preheader (stored column, not scraped from blob)
    let (html, preheader): (String, String) = conn
        .query_row(
            "SELECT html, COALESCE(preheader, '') FROM digests WHERE date = ?1",
            [&date],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .map_err(|_| (StatusCode::NOT_FOUND, "Digest not found"))?;

    // Build OG metadata -- title from digest name + date, preheader from DB column
    let og_title = escape_html(&format!("{} \u{2013} {date}", state.digest_name));
    let og_description = escape_html(&preheader);

    // Build OG tags + favicon
    let canonical_url = state
        .digest_domain
        .as_ref()
        .map(|d| format!("https://{d}/{date}"))
        .unwrap_or_default();
    let og_tags = digest_og_tags(
        &og_title,
        &og_description,
        &canonical_url,
        &state.digest_name,
    );
    let head_inject = format!("{FAVICON_SVG}\n  {og_tags}");

    // Build web footer links (subscribe replaces unsubscribe)
    let (subscribe_url, archive_url) = match &state.digest_domain {
        Some(d) => (format!("https://{d}/#subscribe"), format!("https://{d}")),
        None => ("/#subscribe".to_string(), "/".to_string()),
    };
    let web_footer = web_footer_html(&subscribe_url, &archive_url);

    // Inject elements into stored HTML (warn on miss -- indicates template drift)
    let html = inject(
        &html,
        "</head>",
        &format!("{head_inject}\n{DIGEST_NAV_CSS}</head>"),
        &date,
    );
    let html = inject(&html, "<body>", &format!("<body>{DIGEST_NAV_HTML}"), &date);
    // Insert web links before footer-meta (same position as email links)
    let html = if html.contains(r#"<p class="footer-meta">"#) {
        inject(
            &html,
            r#"<p class="footer-meta">"#,
            &format!("{web_footer}\n    <p class=\"footer-meta\">"),
            &date,
        )
    } else {
        inject(
            &html,
            "</footer>",
            &format!("{web_footer}\n  </footer>"),
            &date,
        )
    };

    Ok(Html(html))
}
