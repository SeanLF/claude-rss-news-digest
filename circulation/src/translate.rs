//! Reader translation: `GET /issues/{date}/translate` -> 307 to a Google Translate
//! proxy URL in the reader's language.
//!
//! Stateless by design (spec §3): no cookie, no storage, no IP read. The one
//! moving part is a redirect whose target is derived from `DIGEST_DOMAIN` and the
//! request's `Accept-Language`. The hit lands in the existing TraceLayer path log,
//! which is the only "is it used?" signal we keep.

use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, header::ACCEPT_LANGUAGE},
    response::Redirect,
};
use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;

use crate::AppState;
use crate::util::is_valid_date;

/// House default translate target when the reader lists no non-English language
/// (or sends no `Accept-Language`). Must never be "en": Google's proxy returns a
/// picker-less "Can't translate this page" error for `tl=en` (see spec §9). Any
/// non-English value works -- the on-page picker lets the reader switch.
const DEFAULT_LANG: &str = "fr";

/// Longest language tag we will splice into a redirect URL (BCP-47 caps a single
/// tag well under this; also bounds untrusted `Accept-Language` input).
const MAX_LANG_TAG_LEN: usize = 35;

/// A BCP-47-shaped token, safe to place in a URL: ASCII alphanumerics and hyphens
/// only, non-empty, bounded length. Rejects `*`, whitespace, and anything that
/// could break out of the query string or the redirect's Location header.
fn is_valid_lang_tag(tag: &str) -> bool {
    !tag.is_empty()
        && tag.len() <= MAX_LANG_TAG_LEN
        && tag.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

/// Longest `?to=` path we will splice into the generic translate redirect. Bounds
/// untrusted query input; every real chrome path is well under this.
const MAX_TRANSLATE_PATH_LEN: usize = 128;

/// Optional `?lang=` override on the translate routes. Lets a *shared* link pin
/// the target language (`/today/translate?lang=fr`) instead of depending on the
/// recipient's `Accept-Language` -- essential for a link the sender chooses the
/// language of. `to` is the page-to-translate path for the generic `/translate`
/// route ([`page_translate`]); the per-date route ignores it.
#[derive(Debug, Default, Deserialize)]
pub struct TranslateQuery {
    pub lang: Option<String>,
    pub to: Option<String>,
}

/// Validate a caller-supplied `?to=` target for safe splicing into the proxy
/// redirect `Location`. Accepts only a same-origin absolute path: a single leading
/// `/` (never `//`, which a browser reads as protocol-relative -> off-site), a
/// bounded length, and a restricted charset (`A-Za-z0-9`, `/`, `-`, `_`) that
/// covers every translatable chrome route (`/`, `/sources`, `/threads`,
/// `/thread/{id}`, `/stats`, `/feedback`) while forbidding the `?`, `#`, `:`, `@`,
/// `\`, `%`, and control chars that could inject a host or break the header.
/// Returns the path only when safe; the caller falls back to `/` otherwise.
pub fn valid_translate_path(path: &str) -> Option<&str> {
    let safe = path.starts_with('/')
        && !path.starts_with("//")
        && path.len() <= MAX_TRANSLATE_PATH_LEN
        && path
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '-' | '_'));
    safe.then_some(path)
}

/// Validate a caller-supplied `?lang=` tag for safe splicing into a redirect
/// `Location`. Returns the tag only when URL-safe (see [`is_valid_lang_tag`]) and
/// non-English -- `tl=en` is Google's picker-less-error case (spec §9), so an
/// English override is treated as "no override" and falls back to detection.
pub fn valid_query_lang(tag: &str) -> Option<&str> {
    let base = tag.split('-').next().unwrap_or("");
    (is_valid_lang_tag(tag) && !base.eq_ignore_ascii_case("en")).then_some(tag)
}

/// Resolve the translate target: an explicit, valid `?lang=` wins; otherwise fall
/// back to `Accept-Language` detection ([`pick_target_lang`]).
fn resolve_target_lang(query_lang: Option<&str>, accept_language: Option<&str>) -> String {
    query_lang
        .and_then(valid_query_lang)
        .map(str::to_string)
        .unwrap_or_else(|| pick_target_lang(accept_language))
}

/// Pick the Google Translate target language from an `Accept-Language` header.
///
/// Iterates the listed tags in order (browsers emit in preference/q order) and
/// returns the first whose base language is not English, verbatim -- Google
/// tolerates region subtags (`fr-CA`≈`fr`) and honours script variants
/// (`zh-CN`/`zh-TW`). Falls back to [`DEFAULT_LANG`] when the header is absent,
/// English-only, or contains only malformed tags. Never returns `en` (spec §9).
fn pick_target_lang(accept_language: Option<&str>) -> String {
    let Some(header) = accept_language else {
        return DEFAULT_LANG.to_string();
    };
    for item in header.split(',') {
        // Strip the ";q=..." weight; keep the bare tag.
        let tag = item.split(';').next().unwrap_or("").trim();
        let base = tag.split('-').next().unwrap_or("");
        if base.eq_ignore_ascii_case("en") || !is_valid_lang_tag(tag) {
            continue;
        }
        return tag.to_string();
    }
    DEFAULT_LANG.to_string()
}

/// Derive the `translate.goog` proxy host for a digest domain: `-` -> `--`, then
/// `.` -> `-`, then append `.translate.goog`. Keeps `DIGEST_DOMAIN` the single
/// source of truth (spec §3.1).
fn proxy_host(domain: &str) -> String {
    // Order matters: double the hyphens first, then map dots to single hyphens.
    let swapped = domain.replace('-', "--").replace('.', "-");
    format!("{swapped}.translate.goog")
}

/// Redirect a same-origin `path` through the `.goog` proxy in the reader's
/// language, or to the untranslated `path` when no `DIGEST_DOMAIN` is set
/// (local/dev has no proxy host, so serve the plain page rather than a broken
/// URL). The single owner of the `_x_tr_*` target form, shared by the per-date
/// and generic translate routes so the two can't drift. Direct `.goog` URL only
/// (the translate.google.com front-door resolves `tl=en` for English-first
/// browsers -> Google's picker-less error). See spec §2.
fn proxy_redirect(
    state: &AppState,
    path: &str,
    query_lang: Option<&str>,
    headers: &HeaderMap,
) -> Redirect {
    let Some(domain) = state.digest_domain.as_deref() else {
        return Redirect::temporary(path);
    };
    let lang = resolve_target_lang(
        query_lang,
        headers.get(ACCEPT_LANGUAGE).and_then(|v| v.to_str().ok()),
    );
    let host = proxy_host(domain);
    Redirect::temporary(&format!(
        "https://{host}{path}?_x_tr_sl=en&_x_tr_tl={lang}&_x_tr_hl={lang}"
    ))
}

/// Redirect `/issues/{date}/translate` to a Google Translate proxy URL in the
/// reader's language (spec §3).
pub async fn translate_redirect(
    Path(date): Path<String>,
    Query(query): Query<TranslateQuery>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Redirect, (StatusCode, &'static str)> {
    if !is_valid_date(&date) {
        return Err((StatusCode::BAD_REQUEST, "Invalid date format"));
    }

    // 404 for a date with no stored digest, so /translate mirrors /{date}.
    let conn = Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "Digest unavailable"))?;
    conn.query_row("SELECT 1 FROM digests WHERE date = ?1", [&date], |row| {
        row.get::<_, i32>(0)
    })
    .map_err(|_| (StatusCode::NOT_FOUND, "Digest not found"))?;

    Ok(proxy_redirect(
        &state,
        &format!("{}/{date}", crate::routes::ISSUES),
        query.lang.as_deref(),
        &headers,
    ))
}

/// Redirect the generic `/translate?to=/path` control to a Google Translate proxy
/// URL for a non-digest chrome page (archive index, sources, threads, stats,
/// feedback). Each such page's Translate pill points here with its own path, so it
/// translates the page the reader is on -- not the latest digest (the digest itself
/// keeps the per-date [`translate_redirect`]). `to` is validated to a safe
/// same-origin path ([`valid_translate_path`]); anything missing or unsafe falls
/// back to the archive index. Same language resolution and `.goog` proxy form as
/// the per-date route; no DB lookup since these pages are always live.
pub async fn page_translate(
    Query(query): Query<TranslateQuery>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Redirect {
    let path = query
        .to
        .as_deref()
        .and_then(valid_translate_path)
        .unwrap_or("/");

    proxy_redirect(&state, path, query.lang.as_deref(), &headers)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::response::IntoResponse;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    // --- pick_target_lang -------------------------------------------------

    #[test]
    fn first_tag_when_top_preference_is_non_english() {
        // fr-FR is first and non-en -> returned verbatim (region subtag kept).
        assert_eq!(pick_target_lang(Some("fr-FR,fr;q=0.9,en;q=0.8")), "fr-FR");
    }

    #[test]
    fn skips_leading_english_to_first_non_english() {
        // en-US and en are skipped; fr (not the top tag) is the first non-en.
        assert_eq!(pick_target_lang(Some("en-US,en;q=0.9,fr;q=0.8")), "fr");
    }

    #[test]
    fn english_only_falls_back_to_default() {
        assert_eq!(pick_target_lang(Some("en-US,en")), DEFAULT_LANG);
    }

    #[test]
    fn missing_header_falls_back_to_default() {
        assert_eq!(pick_target_lang(None), DEFAULT_LANG);
    }

    #[test]
    fn region_subtag_passed_through_verbatim() {
        assert_eq!(pick_target_lang(Some("pt-BR")), "pt-BR");
    }

    #[test]
    fn chinese_script_variant_passed_through_verbatim() {
        assert_eq!(pick_target_lang(Some("zh-TW")), "zh-TW");
    }

    #[test]
    fn never_returns_english_even_when_english_is_the_only_listed_language() {
        // Base-language "en" is excluded regardless of region subtag.
        assert_ne!(pick_target_lang(Some("en-CA,en-GB;q=0.9")), "en");
        assert_ne!(pick_target_lang(Some("EN")), "en");
    }

    #[test]
    fn malformed_and_wildcard_tags_are_skipped() {
        // `*` and header-unsafe tokens must not reach the URL -- fall through.
        assert_eq!(pick_target_lang(Some("*")), DEFAULT_LANG);
        assert_eq!(pick_target_lang(Some("<script>,fr")), "fr");
        assert_eq!(pick_target_lang(Some("de fr")), DEFAULT_LANG);
    }

    // --- proxy_host -------------------------------------------------------

    #[test]
    fn proxy_host_derives_double_hyphen_transform() {
        assert_eq!(
            proxy_host("news-digest.seanfloyd.dev"),
            "news--digest-seanfloyd-dev.translate.goog"
        );
    }

    #[test]
    fn proxy_host_plain_domain() {
        assert_eq!(proxy_host("example.com"), "example-com.translate.goog");
    }

    // --- translate_redirect handler --------------------------------------

    fn state_with_digest(date: &str, domain: Option<&str>) -> Arc<AppState> {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir()
            .join(format!("translate_test_{}_{n}.sqlite", std::process::id()))
            .to_string_lossy()
            .into_owned();
        let conn = Connection::open(&path).expect("open test db");
        conn.execute(
            "CREATE TABLE digests (date TEXT PRIMARY KEY, html TEXT NOT NULL, preheader TEXT DEFAULT '')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO digests (date, html) VALUES (?1, '<html></html>')",
            [date],
        )
        .unwrap();
        drop(conn);

        Arc::new(AppState {
            db_path: path,
            digest_name: "Test Digest".to_string(),
            digest_domain: domain.map(String::from),
            homepage_url: None,
            source_url: None,
            resend_api_key: None,
            resend_audience_id: None,
            from_email: None,
            feedback_email: None,
            font_url: "/assets/fonts/source-serif-4.test.woff2".to_string(),
            http_client: reqwest::Client::new(),
            subscribe_limiter: crate::handlers::RateLimiter::new(
                5,
                std::time::Duration::from_secs(3600),
            ),
            subscribe_token_secret: None,
            double_opt_in: false,
            mcp: Default::default(),
            ask: Default::default(),
        })
    }

    fn headers_with_lang(value: Option<&str>) -> HeaderMap {
        let mut h = HeaderMap::new();
        if let Some(v) = value {
            h.insert(ACCEPT_LANGUAGE, v.parse().unwrap());
        }
        h
    }

    fn location(resp: axum::response::Response) -> String {
        resp.headers()
            .get("location")
            .unwrap()
            .to_str()
            .unwrap()
            .to_string()
    }

    #[tokio::test]
    async fn redirects_to_derived_proxy_url_in_readers_language() {
        let state = state_with_digest("2026-07-03", Some("news-digest.seanfloyd.dev"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("es-ES,es;q=0.9,en;q=0.8")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(
            location(resp),
            "https://news--digest-seanfloyd-dev.translate.goog/issues/2026-07-03?_x_tr_sl=en&_x_tr_tl=es-ES&_x_tr_hl=es-ES"
        );
    }

    #[tokio::test]
    async fn english_reader_lands_on_default_language() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("en-US,en;q=0.9")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(
            location(resp),
            "https://example-com.translate.goog/issues/2026-07-03?_x_tr_sl=en&_x_tr_tl=fr&_x_tr_hl=fr"
        );
    }

    #[tokio::test]
    async fn never_emits_tl_en() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("en-CA")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        let loc = location(resp);
        assert!(!loc.contains("_x_tr_tl=en"), "must never target en: {loc}");
    }

    #[tokio::test]
    async fn invalid_date_returns_400() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let err = translate_redirect(
            Path("not-a-date".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .expect_err("expected rejection");
        assert_eq!(err.0, StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn unknown_digest_returns_404() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let err = translate_redirect(
            Path("2026-07-02".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .expect_err("expected 404");
        assert_eq!(err.0, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn no_domain_falls_back_to_untranslated_digest() {
        // Local/dev has no DIGEST_DOMAIN -> serve the plain digest, not a broken URL.
        let state = state_with_digest("2026-07-03", None);
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            Query(TranslateQuery::default()),
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(location(resp), "/issues/2026-07-03");
    }

    #[tokio::test]
    async fn query_lang_override_wins_over_accept_language() {
        // A shared `/{date}/translate?lang=de` renders in German even for a
        // French-preferring recipient -- the sender chose the language.
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            Query(TranslateQuery {
                lang: Some("de".to_string()),
                to: None,
            }),
            State(state),
            headers_with_lang(Some("fr-FR,fr;q=0.9")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(
            location(resp),
            "https://example-com.translate.goog/issues/2026-07-03?_x_tr_sl=en&_x_tr_tl=de&_x_tr_hl=de"
        );
    }

    #[test]
    fn resolve_target_lang_prefers_valid_query_else_header() {
        // Valid non-en override wins.
        assert_eq!(resolve_target_lang(Some("de"), Some("fr")), "de");
        // English / malformed override is ignored -> header detection.
        assert_eq!(resolve_target_lang(Some("en-US"), Some("es,en")), "es");
        assert_eq!(resolve_target_lang(Some("f r"), Some("it")), "it");
        // No override -> header; no header -> default.
        assert_eq!(resolve_target_lang(None, Some("pt-BR")), "pt-BR");
        assert_eq!(resolve_target_lang(None, None), DEFAULT_LANG);
    }

    #[test]
    fn valid_query_lang_rejects_english_and_unsafe() {
        assert_eq!(valid_query_lang("fr"), Some("fr"));
        assert_eq!(valid_query_lang("zh-TW"), Some("zh-TW"));
        assert_eq!(valid_query_lang("en"), None);
        assert_eq!(valid_query_lang("en-GB"), None);
        assert_eq!(valid_query_lang("fr;drop"), None);
        assert_eq!(valid_query_lang(""), None);
    }

    // --- valid_translate_path --------------------------------------------

    #[test]
    fn valid_translate_path_accepts_known_chrome_routes() {
        for p in [
            "/",
            "/sources",
            "/threads",
            "/thread/12",
            "/stats",
            "/feedback",
        ] {
            assert_eq!(valid_translate_path(p), Some(p), "should accept {p}");
        }
    }

    #[test]
    fn valid_translate_path_rejects_offsite_and_injection() {
        // Protocol-relative -> a browser would navigate off our host.
        assert_eq!(valid_translate_path("//evil.com"), None);
        // Not absolute.
        assert_eq!(valid_translate_path("sources"), None);
        assert_eq!(valid_translate_path(""), None);
        // Chars that could break the URL / inject a host or header.
        assert_eq!(valid_translate_path("/x?y=1"), None);
        assert_eq!(valid_translate_path("/x#frag"), None);
        assert_eq!(valid_translate_path("/x:y"), None);
        assert_eq!(valid_translate_path("/x@y"), None);
        assert_eq!(valid_translate_path("/x\r\nHost: evil"), None);
        assert_eq!(valid_translate_path("/x y"), None);
    }

    // --- page_translate handler ------------------------------------------

    #[tokio::test]
    async fn page_translate_translates_the_requested_page_not_the_digest() {
        let state = state_with_digest("2026-07-03", Some("news-digest.seanfloyd.dev"));
        let resp = page_translate(
            Query(TranslateQuery {
                lang: None,
                to: Some("/sources".to_string()),
            }),
            State(state),
            headers_with_lang(Some("es-ES,es;q=0.9,en;q=0.8")),
        )
        .await
        .into_response();

        assert_eq!(
            location(resp),
            "https://news--digest-seanfloyd-dev.translate.goog/sources?_x_tr_sl=en&_x_tr_tl=es-ES&_x_tr_hl=es-ES"
        );
    }

    #[tokio::test]
    async fn page_translate_defaults_to_index_when_to_missing_or_unsafe() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = page_translate(
            Query(TranslateQuery {
                lang: None,
                to: Some("//evil.com".to_string()),
            }),
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .into_response();

        assert_eq!(
            location(resp),
            "https://example-com.translate.goog/?_x_tr_sl=en&_x_tr_tl=fr&_x_tr_hl=fr"
        );
    }

    #[tokio::test]
    async fn page_translate_no_domain_falls_back_to_the_untranslated_page() {
        // Local/dev has no DIGEST_DOMAIN -> serve the plain page, not a broken URL.
        let state = state_with_digest("2026-07-03", None);
        let resp = page_translate(
            Query(TranslateQuery {
                lang: None,
                to: Some("/threads".to_string()),
            }),
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .into_response();

        assert_eq!(location(resp), "/threads");
    }
}
