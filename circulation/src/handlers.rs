use axum::{
    Form, Json,
    extract::{Path, Query, State},
    http::{
        HeaderMap, HeaderValue, StatusCode,
        header::{ACCEPT, CONTENT_TYPE, LINK, VARY},
    },
    response::{Html, IntoResponse, Redirect, Response},
};
use axum_client_ip::RightmostXForwardedFor;
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use hmac::{Hmac, KeyInit, Mac};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// HMAC-SHA256 used to sign stateless confirmation tokens.
type HmacSha256 = Hmac<Sha256>;

use crate::AppState;
use crate::archive;
use crate::check_database_health;
use crate::feed::{DigestRow, render_atom_feed};
use crate::markdown::{self, Negotiated};
use crate::routes;
use crate::templates::{
    DIGEST_NAV_CSS, FAVICON_SVG, FeedbackParams, IndexParams, NO_FLASH_SCRIPT, NotFoundParams,
    PROXY_TRANSLATE_HIDE_SCRIPT, REDUCED_MOTION_CSS, SKIP_LINK_CSS, SKIP_LINK_HTML, Source,
    SourcesParams, TOGGLE_BTN, TOGGLE_JS, chrome_footer, chrome_topbar, digest_nav_html,
    digest_og_tags, render_feedback, render_index, render_not_found, render_sources,
    translate_pill, web_feedback_html,
};
use crate::util::{escape_html, format_day_month_year, is_valid_date, log_row_error};

/// Most recent digests included in the Atom feed.
const FEED_ENTRY_LIMIT: u32 = 30;

#[derive(Deserialize)]
pub struct SubscribeForm {
    pub email: String,
}

/// Query parameters for the double opt-in confirmation link.
#[derive(Deserialize)]
pub struct ConfirmParams {
    pub token: Option<String>,
}

#[derive(Deserialize, Default)]
pub struct IndexQuery {
    /// `?subscribed=1` — show the subscribe-success notice (form hidden).
    pub subscribed: Option<String>,
    /// `?pending=1` — show the "check your inbox to confirm" notice (double opt-in).
    pub pending: Option<String>,
    /// `?subscribe_invalid=1` — the address was invalid/disposable (user's input, actionable).
    pub subscribe_invalid: Option<String>,
    /// `?subscribe_ratelimited=1` — too many attempts; "try later", not "try again".
    pub subscribe_ratelimited: Option<String>,
    /// `?subscribe_error=1` — a genuine server/Resend failure ("something failed on our end").
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
/// The double opt-in pending notice: signup accepted, awaiting email confirmation.
const CHECK_INBOX_NOTICE: &str = r#"<p class="notice ok" role="status"><span class="ni" aria-hidden="true">✓</span> Almost there — check your inbox (and your spam folder, just in case) and click the link to confirm your subscription.</p>"#;
/// Invalid/disposable address — the user's input, with an actionable next step.
const SUBSCRIBE_INVALID_NOTICE: &str = r#"<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> That address looks invalid or disposable — please try a different email.</p>"#;
/// Rate-limited — "try later" is the correct advice, not "try again" (which won't work now).
const SUBSCRIBE_RATELIMITED_NOTICE: &str = r#"<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> Too many attempts — please try again in a little while.</p>"#;
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
/// "" to keep them all (detail pages). `translate_path` is this page's own site path (e.g.
/// `/sources`, `/thread/12`) so the Translate pill translates the page the reader is on rather than
/// the latest digest. Right cluster = optional Subscribe sublink + Translate pill + theme toggle.
/// Footer = the shared link row + a page-specific `tagline`.
pub(crate) fn sub_chrome(
    state: &AppState,
    current: &str,
    translate_path: &str,
    tagline: &str,
) -> (String, String) {
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
    right.push_str(&translate_pill(&format!("/translate?to={translate_path}")));
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

/// Render the shared friendly 404 page with variant-specific copy, at 404 status.
/// One builder behind both the router fallback (unknown path) and the digest
/// handler's not-found/bad-date branches, so a dead link always lands on the same
/// on-brand page with the ways back rather than an axum default or bare text.
fn render_404(state: &AppState, heading: &str, message: &str) -> Response {
    let (topbar_html, footer_html) = sub_chrome(
        state,
        "",
        "/",
        "Lost? Every issue is one tap from the archive.",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    let html = render_not_found(&NotFoundParams {
        title: &state.digest_name,
        brand_html: &brand,
        home_url: "/",
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        heading,
        message,
        today_url: routes::TODAY,
        search_url: routes::SEARCH,
    });
    (StatusCode::NOT_FOUND, Html(html)).into_response()
}

/// The generic "unknown page" 404 -- shared by the router fallback and every route
/// that rejects a non-date `{date}` segment, so the copy lives in one place.
fn page_not_found(state: &AppState) -> Response {
    render_404(state, "Page not found", "There's nothing at this address.")
}

/// Router fallback for any unmatched path — the friendly 404 (generic variant).
pub async fn not_found(State(state): State<Arc<AppState>>) -> Response {
    page_not_found(&state)
}

/// Index / home — the archive as an issue-numbered running order (`chrome_v12`).
pub async fn index(
    State(state): State<Arc<AppState>>,
    Query(query): Query<IndexQuery>,
    request_headers: HeaderMap,
) -> Result<Response, (StatusCode, &'static str)> {
    let unavailable = |e: (StatusCode, String)| {
        tracing::error!(status = %e.0, detail = %e.1, "index archive query failed");
        (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable")
    };
    let meta = archive::index_meta(&state.db_path).map_err(unavailable)?;

    // Content negotiation: the archive index is also available as Markdown at `/index.md`. Only
    // the default (unfiltered) view negotiates — the paginated `?before=`/`?year=` scopes are the
    // no-JS load-more fallback, not content surfaces an agent would ask for as Markdown.
    let accept = request_headers.get(ACCEPT).and_then(|v| v.to_str().ok());
    let plain_view = query.before.is_none() && query.year.is_none();
    if plain_view {
        match markdown::negotiate(accept) {
            Negotiated::NotAcceptable => return Ok(not_acceptable()),
            Negotiated::Markdown => {
                let page =
                    archive::fetch_archive(&state.db_path, None, None, 100).map_err(unavailable)?;
                let md = markdown::index_markdown(
                    &state.digest_name,
                    &meta,
                    &page.issues,
                    &state.base_url(),
                );
                return Ok(markdown_response(md, &markdown::html_link_header("/")));
            }
            Negotiated::Html => {}
        }
    }

    let subscriptions_enabled =
        state.resend_api_key.is_some() && state.resend_audience_id.is_some();

    let notice_html = if query.subscribed.is_some() {
        SUBSCRIBED_NOTICE
    } else if query.pending.is_some() {
        CHECK_INBOX_NOTICE
    } else if query.subscribe_invalid.is_some() {
        SUBSCRIBE_INVALID_NOTICE
    } else if query.subscribe_ratelimited.is_some() {
        SUBSCRIBE_RATELIMITED_NOTICE
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
    right.push_str(&translate_pill("/translate?to=/"));
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

    // Advertise the Markdown alternate three ways: a `<link>` in the head (DOM crawlers), a
    // visually-hidden pointer inside `<main>` (the human-pastes-URL-into-ChatGPT flow), and a
    // `Link` header + `Vary: Accept` below (headless fetchers + CDN cache correctness).
    let index_md_abs = if state.base_url().is_empty() {
        "/index.md".to_string()
    } else {
        format!("{}/index.md", state.base_url())
    };
    let html = html.replacen(
        "</head>",
        &format!("{}\n</head>", markdown::markdown_link_tag("/index.md")),
        1,
    );
    let html = html.replacen(
        r#"<main id="main">"#,
        &format!(
            r#"<main id="main">{}"#,
            markdown::hidden_pointer(&index_md_abs)
        ),
        1,
    );
    Ok(html_response(
        html,
        &markdown::markdown_link_header("/index.md"),
    ))
}

/// Offline email validation: syntax plus a disposable/throwaway-provider blocklist (2,740+
/// domains), all local -- no third-party verification API, so no subscriber address ever
/// leaves the box. This is hygiene that trims obvious junk; double opt-in is the real
/// anti-abuse control (a bot can't click the confirmation link).
fn is_valid_email(email: &str) -> bool {
    mailchecker::is_valid(email.trim())
}

/// How long a confirmation link stays valid (48h).
const CONFIRM_TOKEN_TTL_SECS: u64 = 48 * 60 * 60;

/// Current unix time in seconds (saturating to 0 before the epoch, which cannot happen).
fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Build a self-contained, signed confirmation token: `b64url(payload).b64url(hmac)`, where
/// `payload = "{email}\n{exp_unix}"` and the HMAC-SHA256 signature proves we issued it.
/// Stateless -- nothing is persisted at signup; the token *is* the pending record.
fn make_token(secret: &[u8], email: &str, exp_unix: u64) -> String {
    let payload = format!("{email}\n{exp_unix}");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(payload.as_bytes());
    let sig = mac.finalize().into_bytes();
    format!("{}.{}", B64.encode(payload.as_bytes()), B64.encode(sig))
}

/// Verify a confirmation token against `secret` at `now_unix`. Returns the email on a valid,
/// unexpired token; `None` on any tamper, bad signature, expiry, or malformed input. The
/// signature check is constant-time (`Mac::verify_slice`).
fn verify_token(secret: &[u8], token: &str, now_unix: u64) -> Option<String> {
    let (payload_b64, sig_b64) = token.split_once('.')?;
    let payload = B64.decode(payload_b64).ok()?;
    let sig = B64.decode(sig_b64).ok()?;
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&payload);
    mac.verify_slice(&sig).ok()?;
    let payload = String::from_utf8(payload).ok()?;
    let (email, exp) = payload.split_once('\n')?;
    let exp: u64 = exp.parse().ok()?;
    if exp <= now_unix {
        return None;
    }
    Some(email.to_string())
}

/// A per-key fixed-window rate limiter. `check` returns `true` when the call is allowed and
/// records it; `false` when the key has already used its quota inside the current window.
pub struct RateLimiter {
    inner: Mutex<HashMap<String, (Instant, u32)>>,
    max: u32,
    window: Duration,
}

impl RateLimiter {
    pub fn new(max: u32, window: Duration) -> Self {
        Self {
            inner: Mutex::new(HashMap::new()),
            max,
            window,
        }
    }

    /// Whether a request keyed by `key` is allowed as of `now` (and count it if so).
    /// `now` is a parameter so the window behaviour is deterministically testable.
    pub fn check(&self, key: &str, now: Instant) -> bool {
        let mut map = self.inner.lock().expect("rate-limiter mutex poisoned");
        // Bound memory: forget keys whose window has fully elapsed.
        map.retain(|_, (start, _)| now.saturating_duration_since(*start) < self.window);
        let entry = map.entry(key.to_string()).or_insert((now, 0));
        if now.saturating_duration_since(entry.0) >= self.window {
            *entry = (now, 0);
        }
        if entry.1 >= self.max {
            return false;
        }
        entry.1 += 1;
        true
    }
}

/// Subscribe handler - adds email to Resend audience
pub async fn subscribe(
    State(state): State<Arc<AppState>>,
    RightmostXForwardedFor(client_ip): RightmostXForwardedFor,
    Form(form): Form<SubscribeForm>,
) -> Redirect {
    // Client IP (rightmost X-Forwarded-For, set by kamal-proxy) for forensics + rate limiting.
    let ip = client_ip.to_string();
    // Lowercase so `Reader@x.com` and `reader@x.com` can't become two separate contacts.
    let email = form.email.trim().to_lowercase();

    // Offline syntax + disposable-domain check before spending any Resend call.
    if !is_valid_email(&email) {
        tracing::info!(ip = %ip, "subscribe rejected: invalid or disposable email");
        return Redirect::to("/?subscribe_invalid=1");
    }

    // Per-IP rate limit: stops one source from bombing signups (and confirmation sends).
    if !state.subscribe_limiter.check(&ip, Instant::now()) {
        tracing::warn!(ip = %ip, email = %email, "subscribe rejected: rate limited");
        return Redirect::to("/?subscribe_ratelimited=1");
    }

    // Double opt-in: email a signed confirmation link and add to the audience only on confirm.
    // (Clicking the link is strong consent evidence. Automated mail link-scanners can fetch the
    // GET link and auto-confirm -- an accepted limitation at this scale; the abuse we actually
    // see is junk-address bombing, whose addresses never click.)
    if let Some(secret) = state.double_opt_in_secret() {
        // A confirmation link must be absolute; without DIGEST_DOMAIN it would be host-less and
        // dead in a mail client. Fail loud rather than mail a link no one can ever use.
        let base = state.base_url();
        if base.is_empty() {
            tracing::error!(
                ip = %ip,
                "double opt-in enabled but DIGEST_DOMAIN unset; cannot build a confirmation link"
            );
            return Redirect::to("/?subscribe_error=1");
        }
        let token = make_token(
            secret.as_bytes(),
            &email,
            now_unix() + CONFIRM_TOKEN_TTL_SECS,
        );
        let confirm_url = format!("{}/confirm?token={}", base.trim_end_matches('/'), token);
        if send_confirmation_email(&state, &email, &confirm_url)
            .await
            .is_err()
        {
            return Redirect::to("/?subscribe_error=1");
        }
        tracing::info!(ip = %ip, email = %email, "subscribe: confirmation email sent");
        return Redirect::to("/?pending=1");
    }

    // Fallback: double opt-in off, or on-but-misconfigured. Warn loudly on the misconfig so a
    // silently-disabled anti-abuse control shows up on every signup, not only once at startup.
    if state.double_opt_in {
        tracing::warn!(
            ip = %ip, email = %email,
            "double opt-in ON but SUBSCRIBE_TOKEN_SECRET missing; adding contact WITHOUT confirmation"
        );
    }
    if add_contact_to_audience(&state, &email).await.is_err() {
        return Redirect::to("/?subscribe_error=1");
    }
    tracing::info!(ip = %ip, email = %email, "subscribe: contact added (direct)");
    Redirect::to("/?subscribed=1")
}

/// Confirmation endpoint for double opt-in. Verifies the signed token from the emailed link
/// and, only then, adds the contact to the Resend audience.
pub async fn confirm(
    State(state): State<Arc<AppState>>,
    RightmostXForwardedFor(client_ip): RightmostXForwardedFor,
    Query(params): Query<ConfirmParams>,
) -> Redirect {
    let ip = client_ip.to_string();

    let Some(secret) = state.subscribe_token_secret.as_ref() else {
        tracing::error!("confirm called but SUBSCRIBE_TOKEN_SECRET is unset");
        return Redirect::to("/?subscribe_error=1");
    };

    let token = params.token.unwrap_or_default();
    let Some(email) = verify_token(secret.as_bytes(), &token, now_unix()) else {
        tracing::info!(ip = %ip, "confirm rejected: invalid or expired token");
        return Redirect::to("/?subscribe_error=1");
    };

    // Re-clicking a still-valid link re-adds the contact; Resend upserts, so this is idempotent.
    if add_contact_to_audience(&state, &email).await.is_err() {
        return Redirect::to("/?subscribe_error=1");
    }
    tracing::info!(ip = %ip, email = %email, "confirm: contact added");
    Redirect::to("/?subscribed=1")
}

/// Send a prepared Resend request; a transport error or any non-2xx status is a failure. `what`
/// names the endpoint in logs ("contacts" / "email"). Logs and returns `Err(())` on failure.
async fn send_resend_request(
    req: reqwest::RequestBuilder,
    email: &str,
    what: &str,
) -> Result<(), ()> {
    let response = match req.send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!(email = %email, "Resend {what} request failed: {e}");
            return Err(());
        }
    };
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        tracing::error!(email = %email, status = %status, body, "Resend {what} API error");
        return Err(());
    }
    Ok(())
}

/// Add a contact to the Resend audience. Shared by the direct-add fallback and `confirm`. Logs
/// and returns `Err(())` on any failure; callers map that to the branded error notice.
async fn add_contact_to_audience(state: &AppState, email: &str) -> Result<(), ()> {
    let Some((api_key, audience_id)) = state
        .resend_api_key
        .as_ref()
        .zip(state.resend_audience_id.as_ref())
    else {
        tracing::error!(email = %email, "cannot add contact: Resend not configured");
        return Err(());
    };

    let url = format!("https://api.resend.com/audiences/{audience_id}/contacts");
    let req = state
        .http_client
        .post(&url)
        .header("Authorization", format!("Bearer {api_key}"))
        .json(&ResendContact {
            email: email.to_string(),
        });
    send_resend_request(req, email, "contacts").await
}

/// Send the double opt-in confirmation email via Resend's transactional Emails API. Logs and
/// returns `Err(())` on any failure; the caller maps that to the branded error notice.
async fn send_confirmation_email(
    state: &AppState,
    email: &str,
    confirm_url: &str,
) -> Result<(), ()> {
    let Some((api_key, from)) = state.resend_api_key.as_ref().zip(state.from_email.as_ref()) else {
        tracing::error!(email = %email, "cannot send confirmation: Resend not configured");
        return Err(());
    };

    let mut payload = serde_json::json!({
        "from": format!("{} <{}>", state.digest_name, from),
        "to": [email],
        "subject": format!("Confirm your subscription to {}", state.digest_name),
        "html": confirmation_email_html(&state.digest_name, confirm_url),
        // A text/plain alternative alongside the HTML: multipart mail is a real deliverability
        // signal (HTML-only scores worse with spam filters) and is what text-mode clients show.
        "text": confirmation_email_text(&state.digest_name, confirm_url),
    });
    // Replies land in the monitored contact inbox, not the (possibly send-only) From address.
    if let Some(reply_to) = state.feedback_email.as_deref() {
        payload["reply_to"] = serde_json::Value::String(reply_to.to_string());
    }

    let req = state
        .http_client
        .post("https://api.resend.com/emails")
        .header("Authorization", format!("Bearer {api_key}"))
        .json(&payload);
    send_resend_request(req, email, "email").await
}

/// Minimal, on-brand HTML for the confirmation email. `confirm_url` is app-generated (URL-safe
/// token) and `digest_name` comes from trusted config, so neither needs escaping.
fn confirmation_email_html(digest_name: &str, confirm_url: &str) -> String {
    format!(
        r#"<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#1a1a1a;">
  <h1 style="font-size:20px;margin:0 0 12px;">Confirm your subscription</h1>
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px;color:#444;">Tap the button to confirm you want the {digest_name} morning briefing in your inbox.</p>
  <p style="margin:0 0 24px;"><a href="{confirm_url}" style="display:inline-block;background:#c45a3b;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;font-size:15px;font-weight:600;">Confirm subscription</a></p>
  <p style="font-size:13px;line-height:1.5;color:#888;margin:0;">If you didn't request this, just ignore this email — you won't be subscribed.</p>
</div>"#
    )
}

/// Plain-text alternative for the confirmation email. Mirrors the HTML's single action so a
/// text-mode client -- and the spam filters that penalise HTML-only mail -- see the same intent
/// and the same link.
fn confirmation_email_text(digest_name: &str, confirm_url: &str) -> String {
    format!(
        "Confirm your subscription\n\n\
         Confirm you want the {digest_name} morning briefing in your inbox by opening this link:\n\
         {confirm_url}\n\n\
         If you didn't request this, just ignore this email — you won't be subscribed.\n"
    )
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

/// Serve robots.txt. The site stays fully crawlable (no AI crawler is blocked) and adds
/// Cloudflare's emerging `Content-Signal` directive (CC0). Three orthogonal signals:
/// `search` (appear in search results), `ai-input` (be used as live AI context — the
/// citation flow this whole feature targets), and `ai-train` (be used for model training).
/// `ai-train=no` is the conservative default for a no-tracking, PolyForm-Noncommercial
/// personal digest: consent to being read and cited, withhold consent to training. Strict
/// robots validators warn about the unknown directive; that's expected and harmless
/// (RFC 9309 says ignore unknown lines). Flip `ai-train` to `yes` if the owner is happy to
/// allow training.
pub async fn robots_txt() -> impl IntoResponse {
    (
        StatusCode::OK,
        [("content-type", "text/plain; charset=utf-8")],
        "User-agent: *\nContent-Signal: search=yes, ai-input=yes, ai-train=no\nAllow: /\n",
    )
}

/// Wrap a Markdown body in a `text/markdown` response carrying `Vary: Accept` (so CDNs cache
/// the HTML and Markdown representations separately) and a `Link` header pointing back at the
/// HTML alternate.
fn markdown_response(body: String, link_header: &str) -> Response {
    let mut resp = ([(CONTENT_TYPE, "text/markdown; charset=utf-8")], body).into_response();
    attach_alternate_headers(&mut resp, link_header);
    resp
}

/// Wrap an HTML body carrying `Vary: Accept` and a `Link` header pointing at the `.md` alternate.
fn html_response(body: String, link_header: &str) -> Response {
    let mut resp = Html(body).into_response();
    attach_alternate_headers(&mut resp, link_header);
    resp
}

/// Add `Vary: Accept` + a single `Link` alternate header to a response.
fn attach_alternate_headers(resp: &mut Response, link_header: &str) {
    let headers = resp.headers_mut();
    headers.insert(VARY, HeaderValue::from_static("accept"));
    match HeaderValue::from_str(link_header) {
        Ok(v) => {
            headers.insert(LINK, v);
        }
        // The Link alternate is this feature's discovery mechanism for headless fetchers; if a
        // bad value (e.g. a misconfigured base_url) makes it unrepresentable, don't drop it silently.
        Err(_) => tracing::warn!(link_header, "alternate Link header rejected; not emitted"),
    }
}

/// `406 Not Acceptable` for a negotiated route whose `Accept` allows neither HTML nor Markdown.
/// Carries `Vary: Accept` and names the two representations rather than substituting silently.
fn not_acceptable() -> Response {
    let mut resp = (
        StatusCode::NOT_ACCEPTABLE,
        "Not Acceptable — this URL is available as text/html or text/markdown.\n",
    )
        .into_response();
    resp.headers_mut()
        .insert(VARY, HeaderValue::from_static("accept"));
    resp
}

/// `GET /llms.txt` — a curated Markdown "README for AI-mediated conversations" (llmstxt.org
/// format). Not a sitemap: it points at the handful of entry points worth ingesting, with
/// absolute links when a domain is configured.
pub async fn llms_txt(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let base = state.base_url();
    let link = |path: &str| {
        if base.is_empty() {
            path.to_string()
        } else {
            format!("{}{path}", base.trim_end_matches('/'))
        }
    };
    let mut body = format!("# {}\n\n", state.digest_name);
    body.push_str(
        "> An automated daily news briefing on geopolitics, tech, and privacy. It reads feeds \
         across five continents, clusters the day's stories, writes a bias-labelled digest, \
         fact-checks itself against the sources, and sends. Curated and written by Claude; no \
         human edits any issue.\n\n",
    );
    body.push_str("## Read the briefing\n\n");
    body.push_str(&format!(
        "- [Latest issue]({}): redirects to the newest dated issue; append `.md` or send \
         `Accept: text/markdown` for Markdown.\n",
        link(routes::TODAY)
    ));
    body.push_str(&format!(
        "- [Archive index]({}): every issue as Markdown, newest first, each linking to its `.md`.\n",
        link("/index.md")
    ));
    body.push_str("\n## Reference\n\n");
    body.push_str(&format!(
        "- [Sources and bias ratings]({}): every source with its Media Bias/Fact Check bias and factuality rating.\n",
        link(routes::SOURCES)
    ));
    body.push_str(&format!(
        "- [Transparency stats]({stats}) ([JSON]({stats_json})): subscriber count, source-spectrum balance, and AI cost per issue.\n",
        stats = link(routes::STATS),
        stats_json = link(&format!("{}.json", routes::STATS)),
    ));
    if let Some(src) = &state.source_url {
        body.push_str(&format!("- [Source code]({src})\n"));
    }
    body.push_str(
        "\nEvery dated issue at `/issues/YYYY-MM-DD` also serves Markdown at `/issues/YYYY-MM-DD.md`.\n",
    );
    ([(CONTENT_TYPE, "text/markdown; charset=utf-8")], body)
}

/// `GET /llms-full.txt` — conservatively redirects to the Markdown archive index rather than
/// concatenating every issue's full text. For a daily news archive that concatenation grows
/// without bound and is mostly stale; the index plus per-issue `.md` covers the same ground on
/// demand. (Open question: for a docs-style site, concatenating is the higher-traffic choice.)
pub async fn llms_full_txt() -> Redirect {
    Redirect::temporary("/index.md")
}

/// `GET /index.md` — the archive running order as Markdown (latest 100 issues), the target the
/// index page's `<link>`/`Link` alternate and `/llms.txt` advertise.
pub async fn index_md(
    State(state): State<Arc<AppState>>,
) -> Result<Response, (StatusCode, &'static str)> {
    let unavailable = |e: (StatusCode, String)| {
        tracing::error!(status = %e.0, detail = %e.1, "index archive query failed");
        (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable")
    };
    let meta = archive::index_meta(&state.db_path).map_err(unavailable)?;
    let page = archive::fetch_archive(&state.db_path, None, None, 100).map_err(unavailable)?;
    let md = markdown::index_markdown(&state.digest_name, &meta, &page.issues, &state.base_url());
    Ok(markdown_response(md, &markdown::html_link_header("/")))
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

/// The outlet's own homepage, derived from the feed URL we poll (the feed URL itself
/// is a machine endpoint, not somewhere to send a reader).
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
        // Google News proxy -- extract the site: operator's domain. The operator sits in
        // a query string, so it ends at the first character a hostname cannot contain:
        // `q=site:reuters.com+when:1d` is a `+`-separated query, and reading to the `&`
        // shipped readers a link to `https://reuters.com+when:1d`.
        if let Some(pos) = rss_url.find("site:") {
            let domain = &rss_url[pos + 5..];
            let end = domain
                .find(|c: char| !(c.is_ascii_alphanumeric() || c == '.' || c == '-'))
                .unwrap_or(domain.len());
            if end > 0 {
                return format!("https://{}", &domain[..end]);
            }
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

/// Sources page -- lists the news sources with bias and factuality ratings.
/// Source data is embedded at compile time from newsroom/sources.json.
///
/// Lists the sources that feed TODAY'S digest, so an entry parked with `"active": false` (a
/// publisher blocking our fetches, say) is left out: claiming a source we no longer read would be
/// the page's one job done wrong. `archive::bias_map` deliberately does the opposite and keeps
/// parked ids, because a past issue really was built from them.
pub async fn sources(
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    static SOURCES_JSON: &str = include_str!("../sources.json");

    fn is_active() -> bool {
        true
    }

    #[derive(Deserialize)]
    struct RawSource {
        id: String,
        name: String,
        url: String,
        bias: String,
        factuality: String,
        perspective: String,
        #[serde(default = "is_active")]
        active: bool,
    }

    let raw: Vec<RawSource> = serde_json::from_str(SOURCES_JSON)
        .map_err(|_| (StatusCode::INTERNAL_SERVER_ERROR, "Bad sources data"))?;
    let raw: Vec<RawSource> = raw.into_iter().filter(|s| s.active).collect();

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
        routes::SOURCES,
        "Bias &amp; factuality ratings via Media Bias/Fact Check; each row links to the outlet.",
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
    Path(raw): Path<String>,
    State(state): State<Arc<AppState>>,
    request_headers: HeaderMap,
) -> Response {
    // An explicit `/issues/{date}.md` URL forces Markdown and overrides `Accept` (explicit URLs
    // win over negotiation, and never 406).
    let (date, explicit_md) = match raw.strip_suffix(".md") {
        Some(d) => (d.to_string(), true),
        None => (raw, false),
    };

    // `/issues/<non-date>` is a mistyped page, not a bad issue date -> generic 404.
    if !is_valid_date(&date) {
        return page_not_found(&state);
    }

    // Decide the representation up front so an unacceptable `Accept` 406s before touching the DB.
    let want = if explicit_md {
        Negotiated::Markdown
    } else {
        markdown::negotiate(request_headers.get(ACCEPT).and_then(|v| v.to_str().ok()))
    };
    if matches!(want, Negotiated::NotAcceptable) {
        return not_acceptable();
    }

    // Open database read-only
    let conn = match Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(%date, error = %e, "digest db open failed");
            return (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable").into_response();
        }
    };

    // Query for digest HTML and preheader (stored column, not scraped from blob).
    // Only a genuinely absent row is a 404 -- any other error (locked/corrupt DB, a
    // truncated db-clone) is a real service failure: log it and return 503, never a
    // friendly "not published" page that lies to the reader and hides the outage.
    let row = conn.query_row(
        "SELECT html, COALESCE(preheader, '') FROM digests WHERE date = ?1",
        [&date],
        |row| Ok((row.get(0)?, row.get(1)?)),
    );
    let (html, preheader): (String, String) = match row {
        Ok(v) => v,
        Err(rusqlite::Error::QueryReturnedNoRows) => {
            let not_here = format!(
                "There's no issue dated {date} in the archive — it may not have been published, or the date is off by a day."
            );
            return render_404(&state, "No issue for that date", &not_here);
        }
        Err(e) => {
            tracing::error!(%date, error = %e, "digest query failed");
            return (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable").into_response();
        }
    };

    // Root-relative + absolute forms of both representations for the alternate `<link>`/headers.
    let md_path = format!("{}/{date}.md", routes::ISSUES);
    let html_path = format!("{}/{date}", routes::ISSUES);
    let md_abs = if state.base_url().is_empty() {
        md_path.clone()
    } else {
        format!("{}{md_path}", state.base_url())
    };

    // Markdown representation: derive it from the stored HTML blob (single source of truth) and
    // point the `Link` header back at the HTML form.
    if matches!(want, Negotiated::Markdown) {
        let Some(md) = markdown::issue_markdown(&html, &state.digest_name, &date) else {
            // The blob exists but derives no body (logged in issue_markdown). Fail loud rather
            // than serve a hollow, title-only document to an agent.
            return (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable").into_response();
        };
        return markdown_response(md, &markdown::html_link_header(&html_path));
    }

    // Build OG metadata -- title from digest name + date, preheader from DB column
    let og_title = escape_html(&format!("{} \u{2013} {date}", state.digest_name));
    let og_description = escape_html(&preheader);

    // Build OG tags + favicon
    let canonical_url = state
        .digest_domain
        .as_ref()
        .map(|d| format!("https://{d}{}/{date}", routes::ISSUES))
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
    // Advertise the Markdown alternate to DOM-parsing crawlers via a head `<link>` (the `Link`
    // header below catches headless fetchers).
    let md_link_tag = markdown::markdown_link_tag(&md_path);
    let head_inject = format!(
        "{color_scheme}\n  {FAVICON_SVG}\n  {og_tags}\n  {md_link_tag}\n  {PROXY_TRANSLATE_HIDE_SCRIPT}\n  {NO_FLASH_SCRIPT}"
    );

    // The digest keeps its OWN <footer> (which already carries a Subscribe link;
    // the email-only Unsubscribe is physically stripped from the stored blob by
    // prepare_for_web). The only web-only footer bit left to fold in is the
    // optional feedback invitation (spec §4).
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
    // Skip link first in <body>, then a visually-hidden pointer to the Markdown version (for the
    // human-pastes-URL-into-ChatGPT flow) -- both before the preheader / paper.
    let html = inject(
        &html,
        "<body>",
        &format!(
            "<body>{SKIP_LINK_HTML}{}",
            markdown::hidden_pointer(&md_abs)
        ),
        &date,
    );
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

    // `Link` header advertising the `.md` alternate + `Vary: Accept` for correct CDN caching.
    html_response(html, &markdown::markdown_link_header(&md_path))
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

    // Root-relative target: same canonical path get_digest serves
    // (`/issues/{date}`), works regardless of whether DIGEST_DOMAIN is configured.
    Ok(Redirect::temporary(&format!("{}/{date}", routes::ISSUES)))
}

/// `/today/translate` -> 307 to the latest digest's `/issues/{date}/translate`, so
/// the stable `today` alias also covers the translate affordance (a shareable
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

    Ok(Redirect::temporary(&format!(
        "{}/{date}/translate{suffix}",
        routes::ISSUES
    )))
}

/// Permanent redirect from the legacy bare-date permalink `/{date}` to its new home
/// `/issues/{date}`. Kept so old email "view in browser" links, RSS `<id>`s, and any
/// shared `/2026-07-03` URLs still resolve after the move. A non-date single segment
/// (`/about`) isn't a moved permalink -> friendly 404, matching `get_digest`.
pub async fn legacy_digest_redirect(
    Path(date): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Response {
    // Preserve a `.md` suffix so legacy `/2026-07-03.md` links reach the Markdown route.
    let (bare, suffix) = match date.strip_suffix(".md") {
        Some(d) => (d, ".md"),
        None => (date.as_str(), ""),
    };
    if !is_valid_date(bare) {
        return page_not_found(&state);
    }
    Redirect::permanent(&format!("{}/{bare}{suffix}", routes::ISSUES)).into_response()
}

/// Permanent redirect from the legacy `/{date}/translate` to `/issues/{date}/translate`,
/// forwarding a valid `?lang=` so shared translate links keep their language.
pub async fn legacy_translate_redirect(
    Path(date): Path<String>,
    State(state): State<Arc<AppState>>,
    Query(query): Query<crate::translate::TranslateQuery>,
) -> Response {
    if !is_valid_date(&date) {
        return page_not_found(&state);
    }
    let suffix = query
        .lang
        .as_deref()
        .and_then(crate::translate::valid_query_lang)
        .map(|l| format!("?lang={l}"))
        .unwrap_or_default();
    Redirect::permanent(&format!("{}/{date}/translate{suffix}", routes::ISSUES)).into_response()
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

    #[test]
    fn confirmation_email_text_carries_the_link_and_intent_without_html() {
        let text = confirmation_email_text(
            "Sean's Digest",
            "https://news-digest.seanfloyd.dev/confirm?token=abc",
        );
        assert!(text.contains("https://news-digest.seanfloyd.dev/confirm?token=abc"));
        assert!(text.contains("Sean's Digest"));
        assert!(text.contains("ignore this email"));
        // A plain-text alternative must not carry HTML markup.
        assert!(!text.contains('<'), "text/plain part should have no tags");
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
            from_email: None,
            feedback_email: None,
            font_url: "/assets/fonts/source-serif-4.test.woff2".to_string(),
            http_client: Client::new(),
            subscribe_limiter: RateLimiter::new(5, Duration::from_secs(3600)),
            subscribe_token_secret: None,
            double_opt_in: false,
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

    #[test]
    fn google_news_site_operator_stops_at_the_query_separator() {
        // Regression: reading to the `&` produced `https://reuters.com+when:1d`, a dead
        // link shipped on /sources. Caught by the Lighthouse crawlable-anchors audit
        // once bin/web-check pointed the gate at the real page instead of the mockups.
        assert_eq!(
            website_from_rss(
                "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en",
                "Reuters"
            ),
            "https://reuters.com"
        );
    }

    #[test]
    fn google_news_site_operator_without_a_time_window_still_works() {
        assert_eq!(
            website_from_rss(
                "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US",
                "AP"
            ),
            "https://apnews.com"
        );
    }

    #[test]
    fn direct_feeds_keep_their_host_and_shed_rss_subdomains() {
        assert_eq!(
            website_from_rss("https://feeds.bbci.co.uk/news/rss.xml", "X"),
            "https://bbci.co.uk"
        );
        assert_eq!(
            website_from_rss("https://www.theguardian.com/world/rss", "X"),
            "https://www.theguardian.com"
        );
    }

    #[test]
    fn every_shipped_source_yields_a_well_formed_homepage() {
        // The gate that matters: no entry in the real sources.json may produce a URL
        // with a character that cannot appear in a host. One bad row is one dead link.
        #[derive(Deserialize)]
        struct Row {
            name: String,
            url: String,
        }
        let rows: Vec<Row> = serde_json::from_str(include_str!("../sources.json")).unwrap();
        for row in &rows {
            let site = website_from_rss(&row.url, &row.name);
            let host = site.strip_prefix("https://").unwrap_or(&site);
            assert!(
                host.chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-'),
                "{} -> {site}",
                row.name
            );
        }
    }

    #[tokio::test]
    async fn sources_page_omits_a_parked_source_that_the_archive_still_counts() {
        // The readers of sources.json disagree ON PURPOSE. /sources answers "what do you read
        // now", so a parked source must not appear. archive::bias_map answers "what was this issue
        // built from", so the same id must still resolve there -- the_hindu coloured 150 archived
        // issues across runs 56-225, and losing it would restate every one of them. stats.rs needs
        // both answers at once; see `parked_sources_leave_the_catalog_but_stay_in_the_history`.
        #[derive(Deserialize)]
        struct Row {
            id: String,
            name: String,
            #[serde(default)]
            active: Option<bool>,
        }
        let rows: Vec<Row> = serde_json::from_str(include_str!("../sources.json")).unwrap();
        let parked: Vec<&Row> = rows.iter().filter(|r| r.active == Some(false)).collect();
        assert!(
            !parked.is_empty(),
            "no parked source in sources.json -- this test is guarding nothing; delete it or park one"
        );

        let state = state_with_digests(&[("2026-06-12", "story")]);
        let html = sources(State(state)).await.expect("sources page renders").0;
        for row in &parked {
            assert!(
                !html.contains(&row.name),
                "{} is parked but still listed on /sources",
                row.name
            );
            assert!(
                crate::archive::bias_map().contains_key(&row.id),
                "{} is parked and the archive can no longer attribute its bias",
                row.id
            );
        }
    }

    #[test]
    fn sub_chrome_translate_pill_targets_the_current_page_not_the_latest_digest() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);
        let (topbar, _footer) = sub_chrome(&state, "sources", routes::SOURCES, "tagline");

        // The pill translates THIS page (/sources), not /today (the latest digest).
        assert!(
            topbar.contains(r#"class="pill" href="/translate?to=/sources""#),
            "expected the current-page translate pill, got: {topbar}"
        );
        assert!(
            !topbar.contains("/today/translate"),
            "must not redirect to the latest digest: {topbar}"
        );
    }

    #[tokio::test]
    async fn not_found_fallback_serves_a_friendly_404_with_ways_back() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);
        let resp = not_found(State(state)).await.into_response();

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(body.contains("Page not found"), "friendly heading missing");
        // The ways back are the stable routes, not a dead end.
        assert!(body.contains(r#"href="/today""#));
        assert!(body.contains(r#"href="/search""#));
    }

    #[tokio::test]
    async fn get_digest_treats_a_non_date_segment_as_a_generic_not_found() {
        // `/about`-style single segments reach get_digest via the `/{date}` route;
        // they're mistyped pages, not bad dates, so they get the generic copy.
        let state = state_with_digests(&[("2026-06-12", "Second story")]);
        let resp = get_digest(Path("about".to_string()), State(state), HeaderMap::new()).await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(body.contains("Page not found"));
        assert!(!body.contains("No issue for that date"));
    }

    #[tokio::test]
    async fn get_digest_renders_friendly_404_for_a_valid_but_absent_date() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);
        // Well-formed date, no such issue -> friendly 404 page, not bare text.
        let resp = get_digest(
            Path("2026-01-01".to_string()),
            State(state),
            HeaderMap::new(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(body.contains("No issue for that date"));
        assert!(body.contains("2026-01-01"));
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
        assert!(body.contains("https://example.com/issues/2026-06-12"));
        assert!(body.contains("https://example.com/issues/2026-06-11"));
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
            from_email: None,
            feedback_email: None,
            font_url: "/assets/fonts/source-serif-4.deadbeef.woff2".to_string(),
            http_client: Client::new(),
            subscribe_limiter: RateLimiter::new(5, Duration::from_secs(3600)),
            subscribe_token_secret: None,
            double_opt_in: false,
        })
    }

    #[tokio::test]
    async fn get_digest_injects_font_face_bound_to_the_hashed_url() {
        let state = state_with_digest_blob(
            "2026-06-12",
            "<html><head><title>x</title></head><body>hi</body></html>",
        );
        let resp = get_digest(
            Path("2026-06-12".to_string()),
            State(state),
            HeaderMap::new(),
        )
        .await;
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
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

    /// A digest blob with a real `<main>` region so Markdown derivation has content to convert.
    const BLOB_WITH_MAIN: &str = "<html><head><title>x</title></head><body><div class=\"paper\">\
        <main id=\"main\"><h2>Must Know</h2><p>A thing happened.</p></main>\
        <footer>foot</footer></div></body></html>";

    fn accept(value: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(ACCEPT, HeaderValue::from_str(value).unwrap());
        h
    }

    #[tokio::test]
    async fn get_digest_html_default_advertises_the_markdown_alternate() {
        let state = state_with_digest_blob("2026-06-12", BLOB_WITH_MAIN);
        let resp = get_digest(
            Path("2026-06-12".to_string()),
            State(state),
            HeaderMap::new(),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK);
        // Vary: Accept + Link header pointing at the .md alternate.
        assert_eq!(resp.headers().get(VARY).unwrap(), "accept");
        let link = resp.headers().get(LINK).unwrap().to_str().unwrap();
        assert!(link.contains("/issues/2026-06-12.md"));
        assert!(link.contains(r#"type="text/markdown""#));
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        // <link> alternate + visually-hidden pointer both present.
        assert!(body.contains(
            r#"<link rel="alternate" type="text/markdown" href="/issues/2026-06-12.md">"#
        ));
        assert!(body.contains("A Markdown version of this page is available at https://example.com/issues/2026-06-12.md."));
    }

    #[tokio::test]
    async fn get_digest_serves_markdown_when_negotiated_and_on_explicit_md_url() {
        // Via Accept.
        let state = state_with_digest_blob("2026-06-12", BLOB_WITH_MAIN);
        let resp = get_digest(
            Path("2026-06-12".to_string()),
            State(state),
            accept("text/markdown"),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(CONTENT_TYPE).unwrap(),
            "text/markdown; charset=utf-8"
        );
        assert_eq!(resp.headers().get(VARY).unwrap(), "accept");
        // .md response links back to the HTML representation.
        let link = resp.headers().get(LINK).unwrap().to_str().unwrap();
        assert!(link.contains(r#"type="text/html""#));
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(body.starts_with("# News Digest — 2026-06-12"));
        assert!(body.contains("Must Know"));
        assert!(body.contains("A thing happened."));
        assert!(!body.contains("<h2>")); // converted, not raw HTML

        // Via explicit .md URL, with no/blank Accept (URL overrides negotiation).
        let state = state_with_digest_blob("2026-06-12", BLOB_WITH_MAIN);
        let resp = get_digest(
            Path("2026-06-12.md".to_string()),
            State(state),
            HeaderMap::new(),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(CONTENT_TYPE).unwrap(),
            "text/markdown; charset=utf-8"
        );
    }

    #[tokio::test]
    async fn get_digest_406_when_neither_html_nor_markdown_acceptable() {
        let state = state_with_digest_blob("2026-06-12", BLOB_WITH_MAIN);
        let resp = get_digest(
            Path("2026-06-12".to_string()),
            State(state),
            accept("application/json"),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::NOT_ACCEPTABLE);
        assert_eq!(resp.headers().get(VARY).unwrap(), "accept");
    }

    #[tokio::test]
    async fn explicit_md_url_never_406s_even_with_hostile_accept() {
        // Explicit .md overrides negotiation: application/json must NOT 406 here.
        let state = state_with_digest_blob("2026-06-12", BLOB_WITH_MAIN);
        let resp = get_digest(
            Path("2026-06-12.md".to_string()),
            State(state),
            accept("application/json"),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(CONTENT_TYPE).unwrap(),
            "text/markdown; charset=utf-8"
        );
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
        assert_eq!(
            response.headers().get("location").unwrap(),
            "/issues/2026-06-12"
        );
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
            "/issues/2026-06-12/translate"
        );
    }

    #[tokio::test]
    async fn today_translate_forwards_valid_lang_override() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);

        let query = crate::translate::TranslateQuery {
            lang: Some("fr".to_string()),
            to: None,
        };
        let response = today_translate(State(state), Query(query))
            .await
            .unwrap()
            .into_response();

        assert_eq!(
            response.headers().get("location").unwrap(),
            "/issues/2026-06-12/translate?lang=fr"
        );
    }

    #[tokio::test]
    async fn today_translate_drops_invalid_lang_override() {
        let state = state_with_digests(&[("2026-06-12", "Second story")]);

        // English (picker-less-error case) and malformed tags are ignored, not spliced.
        for bad in ["en", "en-US", "fr;evil", "  "] {
            let query = crate::translate::TranslateQuery {
                lang: Some(bad.to_string()),
                to: None,
            };
            let response = today_translate(State(state.clone()), Query(query))
                .await
                .unwrap()
                .into_response();
            assert_eq!(
                response.headers().get("location").unwrap(),
                "/issues/2026-06-12/translate",
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

    #[tokio::test]
    async fn legacy_digest_redirect_permanently_moves_bare_date_to_issues() {
        let state = state_with_digests(&[("2026-06-12", "story")]);
        let resp = legacy_digest_redirect(Path("2026-06-12".to_string()), State(state)).await;

        assert_eq!(resp.status(), StatusCode::PERMANENT_REDIRECT);
        assert_eq!(
            resp.headers().get("location").unwrap(),
            "/issues/2026-06-12"
        );
    }

    #[tokio::test]
    async fn legacy_digest_redirect_404s_a_non_date_segment() {
        let state = state_with_digests(&[("2026-06-12", "story")]);
        // A non-date segment is not a moved permalink -> friendly 404, never a redirect.
        let resp = legacy_digest_redirect(Path("about".to_string()), State(state)).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn legacy_digest_redirect_keeps_the_md_suffix_out_of_the_date_check() {
        // The date is validated on the STRIPPED segment and the suffix re-appended in the target.
        // Validate the raw segment instead and every legacy `/2026-07-03.md` link 404s.
        let state = state_with_digests(&[("2026-06-12", "story")]);
        let resp = legacy_digest_redirect(Path("2026-06-12.md".to_string()), State(state)).await;

        assert_eq!(resp.status(), StatusCode::PERMANENT_REDIRECT);
        assert_eq!(
            resp.headers().get("location").unwrap(),
            "/issues/2026-06-12.md"
        );
    }

    #[tokio::test]
    async fn legacy_translate_redirect_moves_to_issues_and_keeps_lang() {
        let state = state_with_digests(&[("2026-06-12", "story")]);
        let query = crate::translate::TranslateQuery {
            lang: Some("fr".to_string()),
            to: None,
        };
        let resp =
            legacy_translate_redirect(Path("2026-06-12".to_string()), State(state), Query(query))
                .await;

        assert_eq!(resp.status(), StatusCode::PERMANENT_REDIRECT);
        assert_eq!(
            resp.headers().get("location").unwrap(),
            "/issues/2026-06-12/translate?lang=fr"
        );
    }
}

/// Feedback page (`GET /feedback`) — a warm mailto CTA. The per-story up/down vote was removed
/// product-wide, so this is a static, read-only page. Kept because already-sent emails link to it;
/// any legacy `?d=&s=&v=` params are simply ignored.
pub async fn feedback(State(state): State<Arc<AppState>>) -> Html<String> {
    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "",
        routes::FEEDBACK,
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

#[cfg(test)]
mod hardening_tests {
    use super::*;

    #[test]
    fn valid_email_accepts_ordinary_address() {
        assert!(is_valid_email("reader@gmail.com"));
    }

    #[test]
    fn valid_email_rejects_malformed() {
        assert!(!is_valid_email("not-an-email"));
        assert!(!is_valid_email(""));
    }

    #[test]
    fn valid_email_rejects_disposable_provider() {
        // mailchecker's offline blocklist covers throwaway providers.
        assert!(!is_valid_email("throwaway@yopmail.com"));
    }

    #[test]
    fn token_round_trips_and_returns_email() {
        let secret = b"top-secret-key";
        let token = make_token(secret, "reader@gmail.com", 2_000);
        assert_eq!(
            verify_token(secret, &token, 1_000),
            Some("reader@gmail.com".to_string())
        );
    }

    #[test]
    fn token_wire_format_is_pinned_to_a_known_vector() {
        // Every other token test round-trips make_token against verify_token, so a change
        // in the signature bytes would pass all of them while silently invalidating every
        // confirmation link already sitting in someone's inbox. This vector was computed
        // OUTSIDE this crate (Python hmac/hashlib over the same payload) so it stays a real
        // check on the crypto stack rather than a snapshot of whatever it currently emits.
        // If a hmac/sha2/base64 bump breaks this, unconfirmed signups break with it.
        let secret = b"top-secret-key";
        let expected = "cmVhZGVyQGdtYWlsLmNvbQoyMDAw.DqdrQT-AEjKtDOxD8rXgJ1hhhVIbS30RaZOrb4hWvko";
        assert_eq!(make_token(secret, "reader@gmail.com", 2_000), expected);
        assert_eq!(
            verify_token(secret, expected, 1_000),
            Some("reader@gmail.com".to_string())
        );
    }

    #[test]
    fn token_rejected_once_expired() {
        let secret = b"top-secret-key";
        let token = make_token(secret, "reader@gmail.com", 2_000);
        assert_eq!(
            verify_token(secret, &token, 2_000),
            None,
            "exp is exclusive"
        );
        assert_eq!(verify_token(secret, &token, 2_001), None);
    }

    #[test]
    fn token_rejected_under_wrong_secret() {
        let token = make_token(b"real-secret", "reader@gmail.com", 2_000);
        assert_eq!(verify_token(b"other-secret", &token, 1_000), None);
    }

    #[test]
    fn token_rejected_when_payload_tampered() {
        let secret = b"top-secret-key";
        let token = make_token(secret, "reader@gmail.com", 2_000);
        let (payload_b64, sig_b64) = token.split_once('.').unwrap();
        // Re-encode a different email under the original signature.
        let forged_payload = B64.encode(b"attacker@evil.com\n2000");
        let forged = format!("{forged_payload}.{sig_b64}");
        assert_eq!(verify_token(secret, &forged, 1_000), None);
        // A flipped signature is likewise rejected.
        let bad_sig = format!("{payload_b64}.{}", B64.encode(b"garbage-signature"));
        assert_eq!(verify_token(secret, &bad_sig, 1_000), None);
    }

    #[test]
    fn token_rejected_when_malformed() {
        let secret = b"top-secret-key";
        assert_eq!(verify_token(secret, "no-dot-separator", 1_000), None);
        assert_eq!(verify_token(secret, "", 1_000), None);
        assert_eq!(verify_token(secret, "!!!.@@@", 1_000), None);
    }

    #[test]
    fn rate_limiter_allows_up_to_max_then_blocks() {
        let rl = RateLimiter::new(3, Duration::from_secs(60));
        let t0 = Instant::now();
        assert!(rl.check("1.2.3.4", t0));
        assert!(rl.check("1.2.3.4", t0));
        assert!(rl.check("1.2.3.4", t0));
        assert!(!rl.check("1.2.3.4", t0), "4th request in window is blocked");
    }

    #[test]
    fn rate_limiter_keys_are_independent() {
        let rl = RateLimiter::new(1, Duration::from_secs(60));
        let t0 = Instant::now();
        assert!(rl.check("1.1.1.1", t0));
        assert!(!rl.check("1.1.1.1", t0));
        // A different IP has its own budget.
        assert!(rl.check("2.2.2.2", t0));
    }

    #[test]
    fn rate_limiter_resets_after_window_elapses() {
        let rl = RateLimiter::new(1, Duration::from_secs(60));
        let t0 = Instant::now();
        assert!(rl.check("1.2.3.4", t0));
        assert!(!rl.check("1.2.3.4", t0));
        // Once the window has fully elapsed, the budget refreshes.
        assert!(rl.check("1.2.3.4", t0 + Duration::from_secs(61)));
    }
}

#[cfg(test)]
mod subscribe_handler_tests {
    use super::*;

    /// An AppState with Resend intentionally unconfigured. Any submission that reaches a Resend
    /// call therefore fails and redirects to the branded error notice -- which lets these tests
    /// prove, without any network, exactly which submissions are short-circuited before that
    /// call and which flow (direct-add vs confirmation-email vs confirm) was taken.
    fn state(
        limiter: RateLimiter,
        secret: Option<&str>,
        double_opt_in: bool,
        domain: Option<&str>,
    ) -> Arc<AppState> {
        Arc::new(AppState {
            db_path: "/tmp/circulation-subscribe-test.db".to_string(),
            digest_name: "News Digest".to_string(),
            digest_domain: domain.map(str::to_string),
            homepage_url: None,
            source_url: None,
            resend_api_key: None,
            resend_audience_id: None,
            from_email: None,
            feedback_email: None,
            font_url: "/assets/fonts/source-serif-4.test.woff2".to_string(),
            http_client: reqwest::Client::new(),
            subscribe_limiter: limiter,
            subscribe_token_secret: secret.map(str::to_string),
            double_opt_in,
        })
    }

    fn ip(addr: &str) -> RightmostXForwardedFor {
        RightmostXForwardedFor(addr.parse().unwrap())
    }

    fn form(email: &str) -> SubscribeForm {
        SubscribeForm {
            email: email.to_string(),
        }
    }

    fn location(redirect: Redirect) -> String {
        redirect
            .into_response()
            .headers()
            .get("location")
            .unwrap()
            .to_str()
            .unwrap()
            .to_string()
    }

    fn rl() -> RateLimiter {
        RateLimiter::new(5, Duration::from_secs(3600))
    }

    #[tokio::test]
    async fn invalid_or_disposable_email_redirects_to_invalid_notice() {
        // Invalid and disposable both land on the actionable "try a different address" notice.
        let st = state(rl(), None, false, Some("example.com"));
        assert_eq!(
            location(subscribe(State(st.clone()), ip("1.2.3.4"), Form(form("not-an-email"))).await),
            "/?subscribe_invalid=1"
        );
        assert_eq!(
            location(subscribe(State(st), ip("1.2.3.4"), Form(form("bot@yopmail.com"))).await),
            "/?subscribe_invalid=1"
        );
    }

    #[tokio::test]
    async fn rate_limited_redirects_to_ratelimited_notice() {
        let st = state(
            RateLimiter::new(1, Duration::from_secs(3600)),
            None,
            false,
            Some("example.com"),
        );
        // First valid attempt passes the limiter, then fails at the unconfigured Resend add.
        assert_eq!(
            location(
                subscribe(
                    State(st.clone()),
                    ip("9.9.9.9"),
                    Form(form("reader@gmail.com"))
                )
                .await
            ),
            "/?subscribe_error=1"
        );
        // Second attempt, same IP, is blocked by the limiter -> its own "try later" notice.
        assert_eq!(
            location(subscribe(State(st), ip("9.9.9.9"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_ratelimited=1"
        );
    }

    #[tokio::test]
    async fn different_ips_have_independent_quota() {
        let st = state(
            RateLimiter::new(1, Duration::from_secs(3600)),
            None,
            false,
            Some("example.com"),
        );
        // One IP exhausts its quota (first passes to the unconfigured add -> error).
        let _ = subscribe(
            State(st.clone()),
            ip("10.0.0.1"),
            Form(form("reader@gmail.com")),
        )
        .await;
        let blocked = location(
            subscribe(
                State(st.clone()),
                ip("10.0.0.1"),
                Form(form("reader@gmail.com")),
            )
            .await,
        );
        assert_eq!(blocked, "/?subscribe_ratelimited=1");
        // A different IP still has budget -> reaches the add (error), not the rate-limit notice.
        assert_eq!(
            location(subscribe(State(st), ip("10.0.0.2"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn double_opt_in_off_valid_email_reaches_direct_add() {
        // Flag off -> direct add; Resend unconfigured -> server-error notice (not /?pending).
        let st = state(rl(), None, false, Some("example.com"));
        assert_eq!(
            location(subscribe(State(st), ip("1.2.3.4"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn double_opt_in_on_valid_email_takes_confirmation_path() {
        // Flag on + secret + domain -> builds the confirm URL and attempts the email send; Resend
        // unconfigured -> server-error. Proves the opt-in branch (with Resend live this mails the
        // link and redirects /?pending=1), not a direct-add success.
        let st = state(rl(), Some("token-secret"), true, Some("example.com"));
        assert_eq!(
            location(subscribe(State(st), ip("1.2.3.4"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn double_opt_in_without_domain_fails_closed_instead_of_mailing_dead_link() {
        // Domain unset -> a confirm link would be host-less; subscribe must refuse, not send.
        let st = state(rl(), Some("token-secret"), true, None);
        assert_eq!(
            location(subscribe(State(st), ip("1.2.3.4"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn misconfigured_flag_on_secret_missing_falls_back_to_direct_add() {
        // Flag on but secret missing -> fail-open to direct add (warned per request); reaches the
        // unconfigured add -> server-error. Never took the opt-in branch (would be /?pending).
        let st = state(rl(), None, true, Some("example.com"));
        assert_eq!(
            location(subscribe(State(st), ip("1.2.3.4"), Form(form("reader@gmail.com"))).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn confirm_rejects_invalid_token() {
        let st = state(rl(), Some("token-secret"), true, Some("example.com"));
        let params = ConfirmParams {
            token: Some("garbage".to_string()),
        };
        assert_eq!(
            location(confirm(State(st), ip("1.2.3.4"), Query(params)).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn confirm_with_valid_token_proceeds_to_audience_add() {
        // A token signed with the state's secret verifies, so confirm proceeds to the (un-
        // configured) audience add -> error. Proves the token was accepted and acted on (the
        // crypto itself is covered exhaustively by the verify_token unit tests).
        let st = state(rl(), Some("token-secret"), true, Some("example.com"));
        let token = make_token(b"token-secret", "reader@gmail.com", now_unix() + 3600);
        let params = ConfirmParams { token: Some(token) };
        assert_eq!(
            location(confirm(State(st), ip("1.2.3.4"), Query(params)).await),
            "/?subscribe_error=1"
        );
    }

    #[tokio::test]
    async fn confirm_without_configured_secret_redirects_to_error() {
        let st = state(rl(), None, true, Some("example.com"));
        let params = ConfirmParams {
            token: Some("anything".to_string()),
        };
        assert_eq!(
            location(confirm(State(st), ip("1.2.3.4"), Query(params)).await),
            "/?subscribe_error=1"
        );
    }
}
