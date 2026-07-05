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
use crate::archive;
use crate::check_database_health;
use crate::feed::{DigestRow, render_atom_feed};
use crate::routes;
use crate::templates::{
    DIGEST_NAV_CSS, FAVICON_SVG, FeedbackParams, IndexParams, NO_FLASH_SCRIPT,
    PROXY_TRANSLATE_HIDE_SCRIPT, REDUCED_MOTION_CSS, SKIP_LINK_CSS, SKIP_LINK_HTML, Source,
    SourcesParams, TOGGLE_BTN, TOGGLE_JS, chrome_footer, chrome_topbar, digest_nav_html,
    digest_og_tags, render_feedback, render_index, render_sources, web_feedback_html,
};
use crate::util::{escape_html, format_day_month_year, is_valid_date, log_row_error};

/// Most recent digests included in the Atom feed.
const FEED_ENTRY_LIMIT: u32 = 30;

#[derive(Deserialize)]
pub struct SubscribeForm {
    pub email: String,
}

#[derive(Deserialize, Default)]
pub struct IndexQuery {
    /// `?subscribed=1` — show the subscribe-success notice (form hidden).
    pub subscribed: Option<String>,
    /// `?subscribe_error=1` — show the subscribe-failure notice (form kept for retry).
    pub subscribe_error: Option<String>,
    /// `?before=<date>` — a discrete older page (the no-JS load-more fallback).
    pub before: Option<String>,
    /// `?year=YYYY` — the "This year" scope rendered as a full page.
    pub year: Option<i64>,
}

#[derive(Serialize)]
struct ResendContact {
    email: String,
}

/// The subscribe-success notice (semantic OK axis, redundant glyph+word).
const SUBSCRIBED_NOTICE: &str = r#"<p class="notice ok" role="status"><span class="ni" aria-hidden="true">✓</span> Subscribed. The next issue will land in your inbox.</p>"#;
/// The subscribe-failure notice (accent = the bad state; assertive; form kept for retry).
const SUBSCRIBE_ERROR_NOTICE: &str = r#"<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> That didn't go through — something failed on our end. Please try again.</p>"#;

/// Brand with the accent word (the last whitespace token) wrapped in `<em>`, e.g. `News <em>Digest</em>`.
pub(crate) fn brand_html(name: &str) -> String {
    match name.trim().rsplit_once(char::is_whitespace) {
        Some((head, last)) => format!("{} <em>{}</em>", escape_html(head), escape_html(last)),
        None => format!("<em>{}</em>", escape_html(name.trim())),
    }
}

/// Group an integer with thousands separators: 3140 -> "3,140".
fn thousands(n: i64) -> String {
    let s = n.abs().to_string();
    let mut out = String::new();
    for (i, ch) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    if n < 0 { format!("-{out}") } else { out }
}

/// The load-more region: the degradable `<a>` (present only when more pages exist; JS intercepts it,
/// no-JS navigates to the discrete `/?before=` page) plus the persistent aria-live status node.
fn loadmore_region(has_more: bool, next_before: &str) -> String {
    let link = if has_more {
        format!(
            r#"<a class="btn secondary" id="loadMore" rel="next" href="/?before={next_before}">Load older issues</a>"#
        )
    } else {
        String::new()
    };
    format!(
        r#"<div class="loadmore" id="loadmore">{link}<p class="loadmore-status" role="status" aria-live="polite"></p></div>"#
    )
}

/// Wrap rendered rows in the index `<ul>` carrying the aria-live "N of TOTAL" denominator.
fn index_list(rows: &str, total: i64) -> String {
    format!(r#"<ul class="index" id="index" data-total="{total}">{rows}</ul>"#)
}

/// Standard sub-page top bar + footer (every chrome page except the index). The nav leads with
/// "← Archive", then the section links with `current` ("sources"|"threads"|"stats") omitted — pass
/// "" to keep them all (detail pages). Right cluster = optional Subscribe sublink + Translate pill +
/// theme toggle. Footer = the shared link row + a page-specific `tagline`.
pub(crate) fn sub_chrome(state: &AppState, current: &str, tagline: &str) -> (String, String) {
    let subscriptions_enabled =
        state.resend_api_key.is_some() && state.resend_audience_id.is_some();
    let mut nav: Vec<(&str, &str)> = vec![("/", "&larr; Archive")];
    for (href, label, key) in [
        (routes::SOURCES, "Sources", "sources"),
        (routes::THREADS, "Threads", "threads"),
        (routes::STATS, "Stats", "stats"),
    ] {
        if key != current {
            nav.push((href, label));
        }
    }
    let mut right = String::new();
    if subscriptions_enabled {
        right.push_str(r##"<a class="sublink" href="/#subscribe">Subscribe</a>"##);
    }
    right.push_str(&format!(
        r#"<a class="pill" href="{}/translate"><span class="g" aria-hidden="true">文A</span> Translate</a>"#,
        routes::TODAY
    ));
    right.push_str(TOGGLE_BTN);
    let topbar = chrome_topbar(&nav, &right);

    let privacy_url = state.privacy_url();
    let mut links: Vec<(&str, &str)> = vec![
        ("/", "Archive"),
        (routes::SOURCES, "Sources"),
        (routes::THREADS, "Threads"),
        (routes::STATS, "Stats"),
        (routes::FEED, "RSS"),
    ];
    if let Some(gh) = &state.source_url {
        links.push((gh.as_str(), "GitHub"));
    }
    links.push((privacy_url.as_str(), "Privacy"));
    if let Some(home) = &state.homepage_url {
        let label = home
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        links.push((home.as_str(), label));
    }
    let footer = chrome_footer(&links, tagline);
    (topbar, footer)
}

/// Index / home — the archive as an issue-numbered running order (`chrome_v12`).
pub async fn index(
    State(state): State<Arc<AppState>>,
    Query(query): Query<IndexQuery>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let unavailable = |_| (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable");
    let meta = archive::index_meta(&state.db_path).map_err(unavailable)?;
    let subscriptions_enabled =
        state.resend_api_key.is_some() && state.resend_audience_id.is_some();

    let notice_html = if query.subscribed.is_some() {
        SUBSCRIBED_NOTICE
    } else if query.subscribe_error.is_some() {
        SUBSCRIBE_ERROR_NOTICE
    } else {
        ""
    };

    // Scope: empty archive → year → discrete ?before= page → the default All first page.
    let (list_html, loadmore_html, segment, has_issues) = if meta.total == 0 {
        let tail = if subscriptions_enabled {
            " — subscribe below and it'll be in your inbox."
        } else {
            "."
        };
        (
            format!(
                r#"<p class="empty">No issues published yet. The first digest lands after the next morning run{tail}</p>"#
            ),
            String::new(),
            "all",
            false,
        )
    } else if let Some(y) = query.year {
        let page =
            archive::fetch_archive(&state.db_path, None, Some(y), 100).map_err(unavailable)?;
        (
            index_list(&archive::rows_html(&page.issues, None), meta.total),
            loadmore_region(false, ""),
            "year",
            true,
        )
    } else if let Some(before) = query.before.as_deref() {
        let page =
            archive::fetch_archive(&state.db_path, Some(before), None, 30).map_err(unavailable)?;
        let region = loadmore_region(page.has_more, page.next_before.as_deref().unwrap_or(""));
        // No-JS discrete page: offer a way back to the newest issues.
        let back = r#"<p style="text-align:center;margin-top:16px"><a href="/">↑ Back to the newest issues</a></p>"#;
        (
            index_list(&archive::rows_html(&page.issues, None), meta.total),
            format!("{region}{back}"),
            "all",
            true,
        )
    } else {
        let page = archive::fetch_archive(&state.db_path, None, None, 30).map_err(unavailable)?;
        (
            index_list(
                &archive::rows_html(&page.issues, meta.newest_date.as_deref()),
                meta.total,
            ),
            loadmore_region(page.has_more, page.next_before.as_deref().unwrap_or("")),
            "all",
            true,
        )
    };

    // Masthead
    let since = meta
        .first_date
        .as_deref()
        .map(format_day_month_year)
        .unwrap_or_default();
    let masthead_stat = format!(
        "<b>{}</b> issues &middot; since {} &middot; <b>{}</b> stories",
        meta.total,
        since,
        thousands(meta.total_stories)
    );
    let brand = brand_html(&state.digest_name);

    // Top bar: nav (index omits Archive, its own section) + right cluster.
    let nav: &[(&str, &str)] = &[
        (routes::SOURCES, "Sources"),
        (routes::THREADS, "Threads"),
        (routes::STATS, "Stats"),
    ];
    let mut right = String::new();
    if subscriptions_enabled {
        right.push_str(r##"<a class="sublink" href="#subscribe">Subscribe</a>"##);
    }
    right.push_str(&format!(
        r#"<a class="pill" href="{}/translate"><span class="g" aria-hidden="true">文A</span> Translate</a>"#,
        routes::TODAY
    ));
    right.push_str(TOGGLE_BTN);
    let topbar_html = chrome_topbar(nav, &right);

    // Footer links (config-dependent).
    let privacy_url = state.privacy_url();
    let mut footer_links: Vec<(&str, &str)> = vec![
        ("/", "Archive"),
        (routes::SOURCES, "Sources"),
        (routes::THREADS, "Threads"),
        (routes::STATS, "Stats"),
        (routes::FEED, "RSS"),
    ];
    if let Some(gh) = &state.source_url {
        footer_links.push((gh.as_str(), "GitHub"));
    }
    footer_links.push((privacy_url.as_str(), "Privacy"));
    if let Some(home) = &state.homepage_url {
        let label = home
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        footer_links.push((home.as_str(), label));
    }
    let footer_html = chrome_footer(
        &footer_links,
        "An automated daily briefing. Curated by Claude, filed by a human. &copy; Sean Floyd",
    );

    let subscribe_band = if subscriptions_enabled {
        r#"<section class="subband" id="subscribe">
      <div class="copy"><h2>Get it in your inbox</h2><p>One briefing each morning. Free, no tracking, unsubscribe anytime.</p></div>
      <form method="post" action="/subscribe" aria-label="Subscribe">
        <input type="email" name="email" placeholder="your@email.com" required aria-label="Email address">
        <button class="btn primary" type="submit">Subscribe</button>
      </form>
    </section>"#
    } else {
        ""
    };

    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    let og_description = "Daily briefing on geopolitics, tech, and privacy. All sides. No fluff.";

    let html = render_index(&IndexParams {
        title: &state.digest_name,
        brand_html: &brand,
        description: og_description,
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        kicker: "Geopolitics &middot; Tech &middot; Privacy &middot; All sides, no fluff",
        masthead_stat: &masthead_stat,
        notice_html,
        has_issues,
        search_url: routes::SEARCH,
        segment,
        date_min: meta.first_date.as_deref().unwrap_or(""),
        date_max: meta.newest_date.as_deref().unwrap_or(""),
        list_html: &list_html,
        loadmore_html: &loadmore_html,
        subscribe_band,
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
            }
        })
        .collect();

    // Sort alphabetically within each bias group
    sources.sort_by_key(|s| s.name.to_lowercase());

    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "sources",
        "Bias &amp; factuality ratings via Ground News; each row links to the outlet.",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    let html = render_sources(&SourcesParams {
        title: &state.digest_name,
        brand_html: &brand,
        home_url: "/",
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        sources: &sources,
    });
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
    // No-flash theme boot joins the favicon/OG/proxy head bundle so the injected
    // theme toggle has a stored preference applied before first paint.
    // color-scheme lets the browser theme form controls/scrollbars for both modes
    // (the web archive has a real light/dark toggle, unlike the light-only email).
    let color_scheme = r#"<meta name="color-scheme" content="light dark">"#;
    let head_inject = format!(
        "{color_scheme}\n  {FAVICON_SVG}\n  {og_tags}\n  {PROXY_TRANSLATE_HIDE_SCRIPT}\n  {NO_FLASH_SCRIPT}"
    );

    // The digest keeps its OWN <footer> (which already carries a Subscribe link;
    // the email-only Unsubscribe is hidden by the web flip). The only web-only
    // footer bit left to fold in is the optional feedback invitation (spec §4).
    let feedback = web_feedback_html(&date, state.feedback_email.as_deref());

    // Foundation plumbing: circulation owns the served font, so it injects the @font-face
    // (family -> the content-hashed /assets route) into the WEB view's head here. Now ACTIVE:
    // the ported digest CSS renders in `var(--serif)` ("Source Serif 4", Georgia, ...), so the
    // web view resolves the family to this hashed route with no hardcoded URL in newsroom. The
    // email render never passes through this handler, so it stays on the Georgia fallback.
    let font_face = crate::assets::font_face(&state.font_url);

    // Inject web chrome into the stored blob (warn on miss -- indicates template drift).
    // The new digest template already ships `<main id="main">` and its own `<footer>`, so we
    // do NOT inject a second main/footer -- only the head bundle, the top bar (inside `.paper`,
    // above the masthead, matching the mockup), the web-only feedback line, and the toggle JS.
    let html = inject(
        &html,
        "</head>",
        &format!(
            "{head_inject}\n{DIGEST_NAV_CSS}\n<style>{font_face}\n{SKIP_LINK_CSS}\n{REDUCED_MOTION_CSS}</style></head>"
        ),
        &date,
    );
    // Skip link first in <body> (before the preheader / paper) -- the first focusable element.
    let html = inject(&html, "<body>", &format!("<body>{SKIP_LINK_HTML}"), &date);
    // Top utility bar inside `.paper`, above the masthead (same column as the content).
    let html = inject(
        &html,
        r#"<div class="paper">"#,
        &format!(r#"<div class="paper">{}"#, digest_nav_html(&date)),
        &date,
    );
    // Web-only feedback line inside the digest's own footer (before the meta rows, else at end).
    let html = if feedback.is_empty() {
        html
    } else if html.contains(r#"<p class="footer-meta">"#) {
        inject(
            &html,
            r#"<p class="footer-meta">"#,
            &format!("{feedback}\n    <p class=\"footer-meta\">"),
            &date,
        )
    } else {
        inject(
            &html,
            "</footer>",
            &format!("{feedback}\n  </footer>"),
            &date,
        )
    };
    // Theme-toggle cycle JS at end of body (drives the injected `#themeBtn`).
    let html = inject(&html, "</body>", &format!("{TOGGLE_JS}</body>"), &date);

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

/// `/today/translate` -> 307 to the latest digest's `/{date}/translate`, so the
/// stable `today` alias also covers the translate affordance (a shareable
/// "translate the latest digest" entrypoint). Mirrors `today`; the follow-up hop
/// re-runs Accept-Language detection with the browser's own headers.
pub async fn today_translate(
    State(state): State<Arc<AppState>>,
    Query(query): Query<crate::translate::TranslateQuery>,
) -> Result<Redirect, (StatusCode, &'static str)> {
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable"))?;

    let date: String = conn
        .query_row(
            "SELECT date FROM digests ORDER BY date DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .map_err(|_| (StatusCode::NOT_FOUND, "No digests yet"))?;

    // Forward a valid `?lang=` so a shared `/today/translate?lang=fr` keeps its
    // language across the hop to the resolved date. Validated here (not spliced
    // raw) so nothing untrusted reaches the Location header.
    let suffix = query
        .lang
        .as_deref()
        .and_then(crate::translate::valid_query_lang)
        .map(|l| format!("?lang={l}"))
        .unwrap_or_default();

    Ok(Redirect::temporary(&format!("/{date}/translate{suffix}")))
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
            font_url: "/assets/fonts/source-serif-4.test.woff2".to_string(),
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

    /// Seed one digest whose stored blob is a full HTML doc (has `</head>`), so the
    /// `get_digest` head-injection actually fires (the shared fixture uses `<html></html>`).
    fn state_with_digest_blob(date: &str, html: &str) -> Arc<AppState> {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let n = TEST_DB_COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!("circulation_blob_test_{nanos}_{n}.db"));
        let db_path = path.to_str().unwrap().to_string();
        let conn = Connection::open(&db_path).unwrap();
        conn.execute(
            "CREATE TABLE digests (date TEXT PRIMARY KEY, html TEXT NOT NULL, preheader TEXT DEFAULT '')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO digests (date, html, preheader) VALUES (?1, ?2, '')",
            [date, html],
        )
        .unwrap();
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
            font_url: "/assets/fonts/source-serif-4.deadbeef.woff2".to_string(),
            http_client: Client::new(),
        })
    }

    #[tokio::test]
    async fn get_digest_injects_font_face_bound_to_the_hashed_url() {
        let state = state_with_digest_blob(
            "2026-06-12",
            "<html><head><title>x</title></head><body>hi</body></html>",
        );
        let resp = get_digest(Path("2026-06-12".to_string()), State(state))
            .await
            .unwrap();
        let body = resp.0; // Html(String)
        // The @font-face binds the family name to the content-hashed /assets route (web view).
        assert!(body.contains("@font-face"));
        assert!(body.contains(r#"font-family:"Source Serif 4""#));
        assert!(
            body.contains(
                r#"src:url("/assets/fonts/source-serif-4.deadbeef.woff2") format("woff2")"#
            )
        );
        // Injected inside <head> (before the close tag), not stray in the body.
        let face_at = body.find("@font-face").unwrap();
        let head_close = body.find("</head>").unwrap();
        assert!(face_at < head_close);
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

    #[tokio::test]
    async fn today_translate_redirects_to_latest_translate_route() {
        let state = state_with_digests(&[
            ("2026-06-11", "First story"),
            ("2026-06-12", "Second story"),
        ]);

        let response = today_translate(State(state), Query(Default::default()))
            .await
            .unwrap()
            .into_response();

        // 307 to the newest digest's per-date translate route.
        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert_eq!(
            response.headers().get("location").unwrap(),
            "/2026-06-12/translate"
        );
    }

    #[tokio::test]
    async fn today_translate_forwards_valid_lang_override() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);

        let query = crate::translate::TranslateQuery {
            lang: Some("fr".to_string()),
        };
        let response = today_translate(State(state), Query(query))
            .await
            .unwrap()
            .into_response();

        assert_eq!(
            response.headers().get("location").unwrap(),
            "/2026-06-12/translate?lang=fr"
        );
    }

    #[tokio::test]
    async fn today_translate_drops_invalid_lang_override() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);

        // English (picker-less-error case) and malformed tags are ignored, not spliced.
        for bad in ["en", "en-US", "fr;evil", "  "] {
            let query = crate::translate::TranslateQuery {
                lang: Some(bad.to_string()),
            };
            let response = today_translate(State(state.clone()), Query(query))
                .await
                .unwrap()
                .into_response();
            assert_eq!(
                response.headers().get("location").unwrap(),
                "/2026-06-12/translate",
                "lang={bad:?} should be dropped"
            );
        }
    }

    #[tokio::test]
    async fn today_translate_returns_404_when_no_digests_exist() {
        let state = state_with_digests(&[]);

        let result = today_translate(State(state), Query(Default::default())).await;

        let (status, _) = result.expect_err("expected 404 when no digests");
        assert_eq!(status, StatusCode::NOT_FOUND);
    }
}

/// Feedback page (`GET /feedback`) — a warm mailto CTA. The per-story up/down vote was removed
/// product-wide, so this is a static, read-only page. Kept because already-sent emails link to it;
/// any legacy `?d=&s=&v=` params are simply ignored.
pub async fn feedback(State(state): State<Arc<AppState>>) -> Html<String> {
    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "",
        "No form, no tracking — feedback goes straight to a human inbox.",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    Html(render_feedback(&FeedbackParams {
        title: &state.digest_name,
        brand_html: &brand,
        home_url: "/",
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        mailto: state.feedback_email.as_deref(),
        today_url: routes::TODAY,
    }))
}

#[cfg(test)]
mod template_seam_tests {
    //! `get_digest` builds the web view by injecting chrome into newsroom's Python-rendered
    //! digest blob, matching exact string needles. Those needles live in a *separate crate and
    //! language* (`newsroom/templates/digest-template.html`), so nothing but this test binds the
    //! two: if the template drifts, `inject()` silently no-ops (warn-only) and the head bundle,
    //! top bar, feedback line, and toggle JS just vanish from the archive. This embeds the real
    //! template at compile time and asserts every needle `get_digest` depends on is present.
    //!
    //! Same repo-root reach as `assets::TOKENS_CSS` (`../../design/tokens.css`); the `ci-rust`
    //! compose service bind-mounts this file into the container the same way it does `tokens.css`.

    const NEWSROOM_TEMPLATE: &str = include_str!("../../newsroom/templates/digest-template.html");

    #[test]
    fn every_get_digest_injection_needle_is_present_in_the_real_template() {
        // Keep in lockstep with the `inject(...)` calls in `get_digest`. A drop here means the
        // corresponding chrome silently disappears from the web archive.
        for needle in [
            "</head>",                    // head bundle: OG + favicon + @font-face + no-flash boot
            "<body>",                     // skip-to-content link
            r#"<div class="paper">"#,     // top utility bar (nav + translate pill + theme toggle)
            r#"<p class="footer-meta">"#, // preferred anchor for the web-only feedback line
            "</footer>",                  // feedback fallback anchor
            "</body>",                    // theme-toggle cycle JS
        ] {
            assert!(
                NEWSROOM_TEMPLATE.contains(needle),
                "web injection needle {needle:?} missing from digest-template.html -- get_digest \
                 would silently drop injected chrome. Reconcile the needle with the template."
            );
        }
    }

    #[test]
    fn footer_meta_needle_binds_the_plain_row_not_the_generated_at_variant() {
        // The template carries both `<p class="footer-meta">` and `<p class="footer-meta
        // generated-at">`. `get_digest` injects the feedback line before the FIRST match; the
        // exact needle must hit the plain row (which precedes the variant) so the feedback line
        // lands above both meta rows, not between them.
        let plain = NEWSROOM_TEMPLATE
            .find(r#"<p class="footer-meta">"#)
            .expect("plain footer-meta anchor must exist");
        let variant = NEWSROOM_TEMPLATE
            .find(r#"<p class="footer-meta generated-at">"#)
            .expect("generated-at footer-meta row must exist");
        assert!(
            plain < variant,
            "plain footer-meta must precede the generated-at variant so the feedback line lands first"
        );
    }
}
