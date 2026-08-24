mod archive;
mod assets;
mod feed;
mod handlers;
mod markdown;
mod search;
mod stats;
mod templates;
mod thread;
mod translate;
mod util;

use axum::Router;
use axum::ServiceExt;
use axum::routing::{get, post};
use handlers::RateLimiter;
use reqwest::Client;
use rusqlite::{Connection, OpenFlags};
use std::sync::Arc;
use std::time::Duration;
use tower::Layer;
use tower_http::normalize_path::NormalizePathLayer;
use tower_http::trace::TraceLayer;

/// Internal route paths -- single source of truth for handlers and templates.
pub mod routes {
    pub const SOURCES: &str = "/sources";
    pub const STATS: &str = "/stats";
    pub const OG_IMAGE: &str = "/og-image.png";
    pub const FEED: &str = "/feed.xml";
    pub const THREAD: &str = "/thread";
    pub const THREADS: &str = "/threads";
    pub const THREADS_MORE: &str = "/threads/more";
    pub const SEARCH: &str = "/search";
    pub const FEEDBACK: &str = "/feedback";
    pub const TODAY: &str = "/today";
    /// Collection prefix for a single dated issue: `/issues/{date}`. The bare
    /// `/{date}` permalink is kept as a permanent redirect into this (legacy links,
    /// RSS ids, old email "view in browser").
    pub const ISSUES: &str = "/issues";
}

pub struct AppState {
    pub db_path: String,
    pub digest_name: String,
    pub digest_domain: Option<String>,
    pub homepage_url: Option<String>,
    pub source_url: Option<String>,
    pub resend_api_key: Option<String>,
    pub resend_audience_id: Option<String>,
    /// The email send address (`RESEND_FROM`) used as the `From` on outbound mail. May be a
    /// send-only address on the sending subdomain, so it is deliberately NOT the reply/contact
    /// target -- see `feedback_email`.
    pub from_email: Option<String>,
    /// Contact address readers reply to and the web `mailto:` points at (`CONTACT_EMAIL`,
    /// falling back to `RESEND_FROM`). A real monitored inbox, distinct from the send-only
    /// `from_email`. Absent -> no `mailto:` line.
    pub feedback_email: Option<String>,
    /// Content-hashed URL of the served woff2 (`/assets/fonts/source-serif-4.{sha}.woff2`),
    /// computed once at startup. Templates emit the `@font-face src` from this; the digest
    /// blob's web view is injected with the matching `@font-face` so all surfaces share one
    /// immutable cached download.
    pub font_url: String,
    pub http_client: Client,
    /// Per-IP rate limiter for the subscribe endpoint. Stops one source from bombing the
    /// Resend audience with junk contacts (see the immenseignite.info spam-signup incident).
    pub subscribe_limiter: RateLimiter,
    /// HMAC secret (`SUBSCRIBE_TOKEN_SECRET`) that signs double opt-in confirmation tokens.
    /// `None` disables signed confirmations regardless of `double_opt_in`.
    pub subscribe_token_secret: Option<String>,
    /// Whether new signups go through double opt-in (`SUBSCRIBE_DOUBLE_OPT_IN`, default true).
    /// The rollback lever: off restores instant direct-add.
    pub double_opt_in: bool,
}

impl AppState {
    /// The signing secret to use when double opt-in is active, else `None` (direct-add
    /// fallback). Active requires both the flag on and a secret present; the flag-on-but-
    /// secret-missing misconfiguration is warned about once at startup, not per request.
    pub fn double_opt_in_secret(&self) -> Option<&str> {
        if self.double_opt_in {
            self.subscribe_token_secret.as_deref()
        } else {
            None
        }
    }

    /// Privacy policy URL, derived from homepage or falling back to a local path.
    pub fn privacy_url(&self) -> String {
        self.homepage_url
            .as_deref()
            .map(|u| format!("{}/privacy", u.trim_end_matches('/')))
            .unwrap_or_else(|| "/privacy".to_string())
    }

    /// Absolute og:image URL, or empty if no domain is configured (OG tags need an absolute URL).
    pub fn og_image_url(&self) -> String {
        self.digest_domain
            .as_ref()
            .map(|d| format!("https://{d}{}", routes::OG_IMAGE))
            .unwrap_or_default()
    }

    /// Scheme+host (e.g. "https://example.com"), or empty string when DIGEST_DOMAIN is
    /// unset (local/dev) -- callers then fall back to root-relative links.
    pub fn base_url(&self) -> String {
        self.digest_domain
            .as_ref()
            .map(|d| format!("https://{d}"))
            .unwrap_or_default()
    }
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
    let digest_domain = std::env::var("DIGEST_DOMAIN").ok();
    let homepage_url = std::env::var("HOMEPAGE_URL").ok();
    let source_url = std::env::var("SOURCE_URL").ok();
    let resend_api_key = std::env::var("RESEND_API_KEY").ok();
    let resend_audience_id = std::env::var("RESEND_AUDIENCE_ID").ok();
    // Send address (the email From). Send-only when it lives on the sending subdomain, so it
    // is NOT where replies or the web `mailto:` should point. Empty -> None so the confirmation
    // guard fails loud rather than sending a "Name <>" From.
    let from_email = std::env::var("RESEND_FROM").ok().filter(|s| !s.is_empty());
    // Contact address: where reader replies and the web `mailto:` go. A real, monitored inbox
    // (`CONTACT_EMAIL`), distinct from the send-only From. `.filter` treats an EMPTY value the
    // same as unset (terraform can interpolate ""), so both fall through to RESEND_FROM rather
    // than yielding a dead mailto / empty Reply-To -- matching the SUBSCRIBE_TOKEN_SECRET read
    // below and the Python side's falsy-empty handling.
    let feedback_email = std::env::var("CONTACT_EMAIL")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("RESEND_FROM").ok().filter(|s| !s.is_empty()));
    let subscribe_token_secret = std::env::var("SUBSCRIBE_TOKEN_SECRET")
        .ok()
        .filter(|s| !s.is_empty());
    // Double opt-in defaults on; only an explicit "false"/"0" disables it.
    let double_opt_in = std::env::var("SUBSCRIBE_DOUBLE_OPT_IN")
        .map(|v| !matches!(v.trim().to_ascii_lowercase().as_str(), "false" | "0" | "no"))
        .unwrap_or(true);
    if double_opt_in && subscribe_token_secret.is_none() {
        tracing::warn!(
            "SUBSCRIBE_DOUBLE_OPT_IN is on but SUBSCRIBE_TOKEN_SECRET is unset; \
             subscribe will fall back to instant direct-add (no confirmation email)"
        );
    }
    if subscribe_token_secret
        .as_deref()
        .is_some_and(|s| s.len() < 16)
    {
        tracing::warn!(
            "SUBSCRIBE_TOKEN_SECRET is shorter than 16 chars; use a high-entropy secret \
             (e.g. `openssl rand -base64 32`)"
        );
    }
    // Double opt-in needs an absolute confirmation URL, which requires DIGEST_DOMAIN. Without it
    // every confirm link is host-less and dead, and subscribe fails closed (see handlers::subscribe).
    if double_opt_in && subscribe_token_secret.is_some() && digest_domain.is_none() {
        tracing::warn!(
            "double opt-in is active but DIGEST_DOMAIN is unset; confirmation links cannot be \
             built and signups will fail until it is set"
        );
    }
    let http_client = Client::new();

    // Fingerprint the compiled-in woff2 once; the route path and every `@font-face src`
    // are built from it so the immutable-cached asset busts whenever the font changes.
    let font_url = assets::font_url(&assets::font_hash());

    let state = Arc::new(AppState {
        db_path,
        digest_name,
        digest_domain,
        homepage_url,
        source_url,
        resend_api_key,
        resend_audience_id,
        from_email,
        feedback_email,
        font_url: font_url.clone(),
        http_client,
        // 5 subscribe attempts per hour per IP: far above any real person (who subscribes
        // once) but tight enough to shut down bombing. Legitimate shared-NAT signups are
        // rare for a personal digest; bump if that ever bites.
        subscribe_limiter: RateLimiter::new(5, Duration::from_secs(3600)),
        subscribe_token_secret,
        double_opt_in,
    });

    let app = Router::new()
        .route("/", get(handlers::index))
        .route("/subscribe", post(handlers::subscribe))
        .route("/confirm", get(handlers::confirm))
        .route("/privacy", get(handlers::privacy))
        .route("/health", get(handlers::health))
        .route("/favicon.ico", get(handlers::favicon))
        .route("/robots.txt", get(handlers::robots_txt))
        // LLM-visibility discovery files + the archive index as Markdown.
        .route("/llms.txt", get(handlers::llms_txt))
        .route("/llms-full.txt", get(handlers::llms_full_txt))
        .route("/index.md", get(handlers::index_md))
        .route("/apple-touch-icon.png", get(handlers::apple_touch_icon))
        .route(
            "/apple-touch-icon-precomposed.png",
            get(handlers::apple_touch_icon),
        )
        .route(routes::OG_IMAGE, get(handlers::og_image))
        .route(routes::SOURCES, get(handlers::sources))
        .route(routes::FEED, get(handlers::feed))
        .route(routes::STATS, get(stats::stats_html))
        .route(&format!("{}.json", routes::STATS), get(stats::stats_json))
        .route(&font_url, get(assets::font))
        .route("/archive", get(archive::archive_fragment))
        .route(routes::THREADS, get(thread::threads_index))
        // Load-more fragment for the "Earlier" list; the index itself also takes the cursor, so
        // this is an enhancement rather than the only way to page.
        .route(routes::THREADS_MORE, get(thread::threads_fragment))
        .route(
            &format!("{}/{{id}}", routes::THREAD),
            get(thread::thread_page),
        )
        .route(routes::SEARCH, get(search::search))
        .route(routes::FEEDBACK, get(handlers::feedback))
        .route(routes::TODAY, get(handlers::today))
        .route("/translate", get(translate::page_translate))
        .route("/today/translate", get(handlers::today_translate))
        .route(
            "/issues/{date}/translate",
            get(translate::translate_redirect),
        )
        .route("/issues/{date}", get(handlers::get_digest))
        // Legacy bare-date permalinks -> permanent redirect into /issues/{date}.
        .route(
            "/{date}/translate",
            get(handlers::legacy_translate_redirect),
        )
        .route("/{date}", get(handlers::legacy_digest_redirect))
        .fallback(handlers::not_found)
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    tracing::info!("digest-circulation listening on {}", addr);

    let app = NormalizePathLayer::trim_trailing_slash().layer(app);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(
        listener,
        ServiceExt::<axum::extract::Request>::into_make_service(app),
    )
    .await
    .unwrap();
}

/// Required tables for the server to function. `story_feedback` was dropped here when the per-story
/// vote was removed product-wide: circulation no longer reads or writes it, so requiring it only
/// broke startup against DBs that don't have it (e.g. the cloud clone `bin/circulation` runs on).
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
