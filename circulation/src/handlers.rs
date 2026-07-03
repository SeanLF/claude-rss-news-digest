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
use crate::feed::{DigestRow, render_atom_feed};
use crate::routes;
use crate::templates::{
    DIGEST_NAV_CSS, FAVICON_SVG, IndexParams, PROXY_TRANSLATE_HIDE_SCRIPT, REDUCED_MOTION_CSS,
    SKIP_LINK_CSS, SKIP_LINK_HTML, Source, digest_nav_html, digest_og_tags, render_feedback_thanks,
    render_index, render_sources, web_footer_html,
};
use crate::util::{
    escape_html, format_date, format_month_year, is_valid_date, log_row_error, year_month,
};

/// Most recent digests included in the Atom feed.
const FEED_ENTRY_LIMIT: u32 = 30;

#[derive(Deserialize)]
pub struct SubscribeForm {
    pub email: String,
}

#[derive(Deserialize, Default)]
pub struct IndexQuery {
    pub subscribed: Option<String>,
}

/// Query params for GET /feedback -- all optional so extraction never fails;
/// validation (and the resulting 400) happens in the handler.
#[derive(Deserialize, Default)]
pub struct FeedbackQuery {
    pub d: Option<String>,
    pub s: Option<String>,
    pub v: Option<String>,
}

/// Cap on the `s` (story) query param length -- generous for a slugified
/// headline, tight enough to keep the column and index sane.
const MAX_STORY_LEN: usize = 200;

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
        <input type="email" name="email" placeholder="your@email.com" required aria-label="Email address">
        <button type="submit">Subscribe</button>
      </form>"#
    } else {
        ""
    };
    let subscribe_teaser = if subscriptions_enabled && !digests.is_empty() {
        // Link the stable /today route rather than baking today's date -- it stays
        // correct as new digests land.
        format!(
            r#"<p class="subscribe-teaser"><a href="{}">Open the latest digest</a> to see what you'd receive daily.</p>"#,
            routes::TODAY
        )
    } else {
        String::new()
    };
    let mut nav_links: Vec<String> = Vec::new();
    if let Some(url) = &state.homepage_url {
        let display = url
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        nav_links.push(format!(
            r#"<a href="{url}" class="meta-link">🌐 {display}</a>"#
        ));
    }
    nav_links.push(format!(
        r#"<a href="{}" class="meta-link">📰 Sources</a>"#,
        routes::SOURCES
    ));
    nav_links.push(format!(
        r#"<a href="{}" class="meta-link">🧵 Threads</a>"#,
        routes::THREADS
    ));
    let privacy_url = state.privacy_url();
    nav_links.push(format!(
        r#"<a href="{privacy_url}" class="meta-link">🔒 Privacy</a>"#
    ));
    if let Some(url) = &state.source_url {
        nav_links.push(format!(
            r#"<a href="{url}" class="meta-link">📦 GitHub</a>"#
        ));
    }
    nav_links.push(format!(
        r#"<a href="{}" class="meta-link">📊 Stats</a>"#,
        routes::STATS
    ));
    let meta_links = format!(
        r#"<nav aria-label="Site navigation"><p class="meta-links">{}</p></nav>"#,
        nav_links.join(" · ")
    );
    let og_description = "Daily briefing on geopolitics, tech, and privacy. All sides. No fluff.";
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();

    let html = render_index(&IndexParams {
        name,
        meta_links: &meta_links,
        success_msg,
        subscribe_form,
        subscribe_teaser: &subscribe_teaser,
        digest_links: &links,
        og_description,
        canonical_url: &canonical_url,
        image_url: &image_url,
        search_url: routes::SEARCH,
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

/// Redirect /privacy to the main site's privacy policy
pub async fn privacy(State(state): State<Arc<AppState>>) -> Redirect {
    // Can't use privacy_url() here: fallback to "/privacy" would loop.
    // HOMEPAGE_URL is always set in production; this hardcodes a safe default.
    let url = state
        .homepage_url
        .as_deref()
        .map(|u| format!("{}/privacy", u.trim_end_matches('/')))
        .unwrap_or_else(|| "https://seanfloyd.dev/privacy".to_string());
    Redirect::temporary(&url)
}

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

const OG_IMAGE_PNG: &[u8] = include_bytes!("../og-image.png");

/// Serve the static branded og:image used for social/chat link previews.
/// Content is a fixed design asset -- immutable long-lived cache is safe.
pub async fn og_image() -> impl IntoResponse {
    (
        StatusCode::OK,
        [
            ("content-type", "image/png"),
            ("cache-control", "public, max-age=31536000, immutable"),
        ],
        OG_IMAGE_PNG,
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

/// Sources page -- lists all news sources with bias and factuality ratings.
/// Source data is embedded at compile time from newsroom/sources.json.
pub async fn sources(
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    static SOURCES_JSON: &str = include_str!("../sources.json");

    #[derive(Deserialize)]
    struct RawSource {
        id: String,
        name: String,
        url: String,
        bias: String,
        factuality: String,
        perspective: String,
    }

    let raw: Vec<RawSource> = serde_json::from_str(SOURCES_JSON)
        .map_err(|_| (StatusCode::INTERNAL_SERVER_ERROR, "Bad sources data"))?;

    use std::collections::HashMap;

    // MBFC slug mapping (source_id -> mbfc slug)
    let mbfc_slugs: HashMap<&str, &str> = [
        ("al_jazeera", "al-jazeera"),
        ("al_monitor", "al-monitor"),
        ("ars_technica", "ars-technica"),
        ("bbc_world", "bbc"),
        ("cbc_news", "cbc-news-canadian-broadcasting"),
        ("daily_maverick", "daily-maverick"),
        ("der_spiegel", "spiegel-online"),
        ("deutsche_welle", "dw-news"),
        ("financial_times", "financial-times"),
        ("globe_and_mail", "the-globe-and-mail"),
        ("le_monde", "le-monde"),
        ("nikkei_asia", "nikkei"),
        ("npr_world", "npr"),
        ("nyt_world", "new-york-times"),
        ("rappler", "rappler"),
        ("rest_of_world", "rest-of-world"),
        ("reuters", "reuters"),
        ("straits_times", "the-straits-times"),
        ("the_diplomat", "the-diplomat"),
        ("the_guardian", "the-guardian"),
        ("the_hindu", "the-hindu"),
        ("the_verge", "the-verge"),
        ("washington_post", "washington-post"),
        ("wsj_world", "wall-street-journal"),
        ("haaretz_middle_east", "haaretz"),
        ("haaretz_world", "haaretz"),
        ("scmp_asia", "south-china-morning-post"),
        ("scmp_china", "south-china-morning-post"),
        ("scmp_world", "south-china-morning-post"),
        ("economist_americas", "the-economist"),
        ("economist_asia", "the-economist"),
        ("economist_europe", "the-economist"),
        ("economist_international", "the-economist"),
        ("economist_middle_east_africa", "the-economist"),
    ]
    .into_iter()
    .collect();

    // Deduplicate multi-feed sources: group by (name, bias) and count feeds
    let mut seen: HashMap<String, (RawSource, u32)> = HashMap::new();
    for s in raw {
        let dedup_key = match s.id.as_str() {
            id if id.starts_with("economist_") => "Economist".to_string(),
            id if id.starts_with("scmp_") => "SCMP".to_string(),
            id if id.starts_with("haaretz_") => "Haaretz".to_string(),
            _ => s.name.clone(),
        };
        seen.entry(dedup_key)
            .and_modify(|(_, count)| *count += 1)
            .or_insert((s, 1));
    }

    // Extract website from RSS URL (strip path for display)
    fn website_from_rss(rss_url: &str, name: &str) -> String {
        // Special cases where RSS URL doesn't match the outlet's website
        match name {
            "BBC World" => return "https://www.bbc.com".to_string(),
            "Hacker News" => return "https://news.ycombinator.com".to_string(),
            "Nikkei Asia" => return "https://asia.nikkei.com".to_string(),
            "Wall Street Journal" => return "https://www.wsj.com".to_string(),
            _ => {}
        }
        if rss_url.starts_with("https://news.google.com") {
            // Google News proxy -- extract site: param
            if let Some(pos) = rss_url.find("site:") {
                let domain = &rss_url[pos + 5..];
                let end = domain.find('&').unwrap_or(domain.len());
                return format!("https://{}", &domain[..end]);
            }
        }
        // Extract scheme + host from URL
        if let Some(rest) = rss_url.strip_prefix("https://") {
            let host_end = rest.find('/').unwrap_or(rest.len());
            let host = &rest[..host_end];
            // Strip common RSS subdomains
            let host = host
                .strip_prefix("feeds.")
                .or_else(|| host.strip_prefix("rss."))
                .unwrap_or(host);
            format!("https://{host}")
        } else {
            rss_url.to_string()
        }
    }

    let mut sources: Vec<Source> = seen
        .into_values()
        .map(|(raw, feed_count)| {
            let mbfc_slug = mbfc_slugs.get(raw.id.as_str()).unwrap_or(&"").to_string();
            let website = website_from_rss(&raw.url, &raw.name);
            // Clean up display name (strip feed suffixes)
            let display_name = match raw.id.as_str() {
                id if id.starts_with("economist_") => "The Economist".to_string(),
                id if id.starts_with("scmp_") => "South China Morning Post".to_string(),
                id if id.starts_with("haaretz_") => "Haaretz".to_string(),
                _ => raw.name,
            };
            fn capitalise_word(w: &str) -> String {
                let mut chars = w.chars();
                match chars.next() {
                    None => String::new(),
                    Some(first) => first.to_uppercase().to_string() + chars.as_str(),
                }
            }
            let perspective = raw
                .perspective
                .split('_')
                .map(capitalise_word)
                .collect::<Vec<_>>()
                .join(" ");
            Source {
                name: display_name,
                website,
                bias: raw.bias,
                factuality: raw.factuality,
                perspective,
                feed_count,
                mbfc_slug,
            }
        })
        .collect();

    // Sort alphabetically within each bias group
    sources.sort_by_key(|s| s.name.to_lowercase());

    let html = render_sources(&state.digest_name, &sources, state.source_url.as_deref());
    Ok(Html(html))
}

/// Serve an Atom 1.0 feed of the most recent digests, for feed reader auto-discovery.
pub async fn feed(
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, (StatusCode, &'static str)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?;

    let mut stmt = conn
        .prepare("SELECT date, COALESCE(preheader, '') FROM digests ORDER BY date DESC LIMIT ?1")
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?;

    let rows: Vec<DigestRow> = stmt
        .query_map([FEED_ENTRY_LIMIT], |row| {
            Ok(DigestRow {
                date: row.get(0)?,
                preheader: row.get(1)?,
            })
        })
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"))?
        .filter_map(|r| log_row_error(r, "digests"))
        .collect();

    // Feed-level <updated> is required by Atom; fall back to the epoch when there are no
    // digests yet rather than omitting the element.
    let updated = rows
        .first()
        .map(|r| format!("{}T00:00:00Z", r.date))
        .unwrap_or_else(|| "1970-01-01T00:00:00Z".to_string());

    let xml = render_atom_feed(&state.digest_name, &state.base_url(), &updated, &rows);

    Ok((
        StatusCode::OK,
        [("content-type", "application/atom+xml; charset=utf-8")],
        xml,
    ))
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
    let image_url = state.og_image_url();
    let og_tags = digest_og_tags(
        &og_title,
        &og_description,
        &canonical_url,
        &state.digest_name,
        &image_url,
    );
    let head_inject = format!("{FAVICON_SVG}\n  {og_tags}\n  {PROXY_TRANSLATE_HIDE_SCRIPT}");

    // Build web footer links (subscribe replaces unsubscribe)
    let (subscribe_url, archive_url) = match &state.digest_domain {
        Some(d) => (format!("https://{d}/#subscribe"), format!("https://{d}")),
        None => ("/#subscribe".to_string(), "/".to_string()),
    };
    let web_footer = web_footer_html(
        &subscribe_url,
        &archive_url,
        routes::SOURCES,
        &state.privacy_url(),
        &date,
        state.feedback_email.as_deref(),
    );

    // Inject elements into stored HTML (warn on miss -- indicates template drift)
    let html = inject(
        &html,
        "</head>",
        &format!(
            "{head_inject}\n{DIGEST_NAV_CSS}\n<style>{SKIP_LINK_CSS}\n{REDUCED_MOTION_CSS}</style></head>"
        ),
        &date,
    );
    let html = inject(
        &html,
        "<body>",
        &format!(
            "<body>{SKIP_LINK_HTML}{}<main id=\"main\">",
            digest_nav_html(&date)
        ),
        &date,
    );
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
    let html = inject(&html, "</body>", "</main></body>", &date);

    Ok(Html(html))
}

/// Redirect /today to the most recent digest -- a stable, bookmarkable URL.
pub async fn today(
    State(state): State<Arc<AppState>>,
) -> Result<Redirect, (StatusCode, &'static str)> {
    // Open database read-only
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable"))?;

    // Resolve the latest digest date; QueryReturnedNoRows when the table is empty
    let date: String = conn
        .query_row(
            "SELECT date FROM digests ORDER BY date DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .map_err(|_| (StatusCode::NOT_FOUND, "No digests yet"))?;

    // Root-relative target: same canonical path get_digest serves (`/{date}`),
    // works regardless of whether DIGEST_DOMAIN is configured.
    Ok(Redirect::temporary(&format!("/{date}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;

    const PNG_MAGIC: [u8; 8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];

    #[tokio::test]
    async fn og_image_serves_png_bytes_with_long_cache_header() {
        let response = og_image().await.into_response();
        assert_eq!(response.status(), StatusCode::OK);

        let headers = response.headers();
        assert_eq!(headers.get("content-type").unwrap(), "image/png");
        let cache_control = headers.get("cache-control").unwrap().to_str().unwrap();
        assert!(cache_control.contains("max-age"));

        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        assert!(body.len() > 8, "og-image.png body should not be empty");
        assert_eq!(
            &body[..8],
            &PNG_MAGIC,
            "body should start with PNG magic bytes"
        );
    }
}

#[cfg(test)]
mod feed_tests {
    use super::*;
    use reqwest::Client;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static TEST_DB_COUNTER: AtomicU32 = AtomicU32::new(0);

    /// Create a throwaway sqlite DB with a `digests` table seeded with `rows`, and an
    /// `AppState` pointing at it. The file is left on disk under the OS temp dir (test dirs
    /// are cheap and unique per-call, no cross-test collisions).
    fn state_with_digests(rows: &[(&str, &str)]) -> Arc<AppState> {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let n = TEST_DB_COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!("circulation_feed_test_{nanos}_{n}.db"));
        let db_path = path.to_str().unwrap().to_string();

        let conn = Connection::open(&db_path).unwrap();
        conn.execute(
            "CREATE TABLE digests (date TEXT PRIMARY KEY, html TEXT NOT NULL, preheader TEXT DEFAULT '')",
            [],
        )
        .unwrap();
        for (date, preheader) in rows {
            conn.execute(
                "INSERT INTO digests (date, html, preheader) VALUES (?1, '<html></html>', ?2)",
                [date, preheader],
            )
            .unwrap();
        }
        drop(conn);

        Arc::new(AppState {
            db_path,
            digest_name: "News Digest".to_string(),
            digest_domain: Some("example.com".to_string()),
            homepage_url: None,
            source_url: None,
            resend_api_key: None,
            resend_audience_id: None,
            feedback_email: None,
            http_client: Client::new(),
        })
    }

    async fn feed_response_text(state: Arc<AppState>) -> (StatusCode, Option<String>, String) {
        let response = feed(State(state)).await.unwrap().into_response();
        let status = response.status();
        let content_type = response
            .headers()
            .get("content-type")
            .map(|v| v.to_str().unwrap().to_string());
        let body_bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body_bytes.to_vec()).unwrap();
        (status, content_type, body)
    }

    #[tokio::test]
    async fn feed_parses_as_xml_with_seeded_entries() {
        let state = state_with_digests(&[
            ("2026-06-12", "Second story"),
            ("2026-06-11", "First story"),
        ]);

        let (status, content_type, body) = feed_response_text(state).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            content_type.as_deref(),
            Some("application/atom+xml; charset=utf-8")
        );
        assert!(body.starts_with(r#"<?xml version="1.0" encoding="utf-8"?>"#));
        assert_eq!(body.matches("<entry>").count(), 2);
        assert!(body.contains("https://example.com/2026-06-12"));
        assert!(body.contains("https://example.com/2026-06-11"));
        // Newest first.
        assert!(body.find("2026-06-12").unwrap() < body.find("2026-06-11").unwrap());
    }

    #[tokio::test]
    async fn feed_escapes_special_characters_from_the_db() {
        let state = state_with_digests(&[("2026-06-12", "Cats & dogs <fight> today")]);

        let (_, _, body) = feed_response_text(state).await;

        assert!(!body.contains("<fight>"));
        assert!(body.contains("Cats &amp; dogs &lt;fight&gt; today"));
    }

    #[tokio::test]
    async fn feed_is_empty_but_valid_with_no_digests() {
        let state = state_with_digests(&[]);

        let (status, _, body) = feed_response_text(state).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(body.matches("<entry>").count(), 0);
        assert!(body.contains("<feed xmlns=\"http://www.w3.org/2005/Atom\">"));
    }

    #[tokio::test]
    async fn today_redirects_to_the_most_recent_digest() {
        let state = state_with_digests(&[
            ("2026-06-11", "First story"),
            ("2026-06-12", "Second story"),
        ]);

        let response = today(State(state)).await.unwrap().into_response();

        // Redirect::temporary -> 307, Location points at the newest digest.
        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert_eq!(response.headers().get("location").unwrap(), "/2026-06-12");
    }

    #[tokio::test]
    async fn today_returns_404_when_no_digests_exist() {
        let state = state_with_digests(&[]);

        let result = today(State(state)).await;

        let (status, _) = result.expect_err("expected 404 when no digests");
        assert_eq!(status, StatusCode::NOT_FOUND);
    }
}

/// Record a one-click per-story feedback vote (GET /feedback?d=&s=&v=up|down).
///
/// GET-with-side-effect is a known tradeoff here: mail scanners that prefetch
/// links can create noise votes. Acceptable at this list's size; dedup can
/// come later if it becomes a problem.
pub async fn feedback(
    State(state): State<Arc<AppState>>,
    Query(query): Query<FeedbackQuery>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let date = query
        .d
        .as_deref()
        .filter(|d| is_valid_date(d))
        .ok_or((StatusCode::BAD_REQUEST, "Invalid or missing date"))?;
    let vote = query
        .v
        .as_deref()
        .filter(|v| *v == "up" || *v == "down")
        .ok_or((StatusCode::BAD_REQUEST, "Invalid or missing vote"))?;
    let story = query
        .s
        .as_deref()
        .filter(|s| !s.is_empty() && s.chars().count() <= MAX_STORY_LEN)
        .ok_or((StatusCode::BAD_REQUEST, "Invalid or missing story"))?;

    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| {
            tracing::error!("Failed to open database for feedback write: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                "Feedback service unavailable",
            )
        })?;
    // Mirror Python's sqlite3 default (5s busy timeout); circulation is now a
    // second writer alongside the newsroom pipeline, and rusqlite defaults to 0ms.
    conn.busy_timeout(std::time::Duration::from_secs(5))
        .map_err(|e| {
            tracing::error!("Failed to set busy_timeout for feedback write: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                "Feedback service unavailable",
            )
        })?;

    conn.execute(
        "INSERT INTO story_feedback (digest_date, story, vote) VALUES (?1, ?2, ?3)",
        (date, story, vote),
    )
    .map_err(|e| {
        tracing::error!("Failed to record feedback: {e}");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "Failed to record feedback",
        )
    })?;

    Ok(Html(render_feedback_thanks(
        &state.digest_name,
        date,
        story,
    )))
}

#[cfg(test)]
mod feedback_tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// Build an AppState pointed at `db_path` -- every other field is a fixed
    /// stub, irrelevant to the feedback handler under test.
    fn build_state(db_path: impl Into<String>) -> Arc<AppState> {
        Arc::new(AppState {
            db_path: db_path.into(),
            digest_name: "Test Digest".to_string(),
            digest_domain: None,
            homepage_url: None,
            source_url: None,
            resend_api_key: None,
            resend_audience_id: None,
            feedback_email: None,
            http_client: reqwest::Client::new(),
        })
    }

    /// Build an AppState backed by a throwaway sqlite file with just the
    /// story_feedback table -- enough for the handler under test.
    fn test_state() -> (Arc<AppState>, String) {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir()
            .join(format!("feedback_test_{}_{n}.sqlite", std::process::id()))
            .to_string_lossy()
            .into_owned();

        let conn = Connection::open(&path).expect("open test db");
        conn.execute_batch(
            "CREATE TABLE story_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                digest_date TEXT NOT NULL,
                story TEXT NOT NULL,
                vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
            );",
        )
        .expect("create test schema");

        (build_state(path.clone()), path)
    }

    fn feedback_count(path: &str) -> i64 {
        let conn = Connection::open(path).expect("open test db");
        conn.query_row("SELECT COUNT(*) FROM story_feedback", [], |row| row.get(0))
            .expect("count rows")
    }

    fn query(d: Option<&str>, s: Option<&str>, v: Option<&str>) -> Query<FeedbackQuery> {
        Query(FeedbackQuery {
            d: d.map(String::from),
            s: s.map(String::from),
            v: v.map(String::from),
        })
    }

    #[tokio::test]
    async fn valid_vote_inserts_and_renders_thanks() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some("some-story-slug"), Some("up")),
        )
        .await;

        let Html(body) = result.expect("expected success");
        assert!(body.contains("Thanks"));
        assert!(body.contains("some-story-slug"));
        assert!(body.contains("/2026-07-01"));
        assert_eq!(feedback_count(&path), 1);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn down_vote_inserts() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some("some-story-slug"), Some("down")),
        )
        .await;

        assert!(result.is_ok());
        assert_eq!(feedback_count(&path), 1);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn invalid_date_rejected_without_insert() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(Some("not-a-date"), Some("story"), Some("up")),
        )
        .await;

        let err = result.expect_err("expected rejection");
        assert_eq!(err.0, StatusCode::BAD_REQUEST);
        assert_eq!(feedback_count(&path), 0);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn missing_date_rejected_without_insert() {
        let (state, path) = test_state();
        let result = feedback(State(state), query(None, Some("story"), Some("up"))).await;

        assert_eq!(
            result.expect_err("expected rejection").0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(feedback_count(&path), 0);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn invalid_vote_rejected_without_insert() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some("story"), Some("sideways")),
        )
        .await;

        assert_eq!(
            result.expect_err("expected rejection").0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(feedback_count(&path), 0);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn empty_story_rejected_without_insert() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some(""), Some("up")),
        )
        .await;

        assert_eq!(
            result.expect_err("expected rejection").0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(feedback_count(&path), 0);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn oversized_story_rejected_without_insert() {
        let (state, path) = test_state();
        let oversized = "a".repeat(MAX_STORY_LEN + 1);
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some(&oversized), Some("up")),
        )
        .await;

        assert_eq!(
            result.expect_err("expected rejection").0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(feedback_count(&path), 0);
        std::fs::remove_file(&path).ok();
    }

    #[tokio::test]
    async fn db_open_failure_returns_500_not_fake_success() {
        let state = build_state("/nonexistent/does-not-exist.sqlite");
        let result = feedback(
            State(state),
            query(Some("2026-07-01"), Some("story"), Some("up")),
        )
        .await;

        assert_eq!(
            result
                .expect_err("expected failure, not a fake success page")
                .0,
            StatusCode::INTERNAL_SERVER_ERROR
        );
    }

    #[tokio::test]
    async fn story_is_escaped_in_thanks_page() {
        let (state, path) = test_state();
        let result = feedback(
            State(state),
            query(
                Some("2026-07-01"),
                Some("<script>alert(1)</script>"),
                Some("up"),
            ),
        )
        .await;

        let Html(body) = result.expect("expected success");
        assert!(!body.contains("<script>alert"));
        assert!(body.contains("&lt;script&gt;"));
        std::fs::remove_file(&path).ok();
    }
}
