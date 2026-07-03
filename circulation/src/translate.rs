//! Reader translation: `GET /{date}/translate` -> 307 to a Google Translate
//! proxy URL in the reader's language.
//!
//! Stateless by design (spec §3): no cookie, no storage, no IP read. The one
//! moving part is a redirect whose target is derived from `DIGEST_DOMAIN` and the
//! request's `Accept-Language`. The hit lands in the existing TraceLayer path log,
//! which is the only "is it used?" signal we keep.

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode, header::ACCEPT_LANGUAGE},
    response::Redirect,
};
use rusqlite::{Connection, OpenFlags};

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

/// Redirect `/{date}/translate` to a Google Translate proxy URL in the reader's
/// language (spec §3).
pub async fn translate_redirect(
    Path(date): Path<String>,
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

    // No DIGEST_DOMAIN (local/dev): there is no proxy host to build, so fall back
    // to the untranslated digest rather than emit a broken URL.
    let Some(domain) = state.digest_domain.as_deref() else {
        return Ok(Redirect::temporary(&format!("/{date}")));
    };

    let lang = pick_target_lang(headers.get(ACCEPT_LANGUAGE).and_then(|v| v.to_str().ok()));
    let host = proxy_host(domain);
    // Direct .goog URL only (the translate.google.com front-door resolves tl=en
    // for English-first browsers -> Google's picker-less error). See spec §2.
    let target = format!("https://{host}/{date}?_x_tr_sl=en&_x_tr_tl={lang}&_x_tr_hl={lang}");
    Ok(Redirect::temporary(&target))
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
            feedback_email: None,
            http_client: reqwest::Client::new(),
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
            State(state),
            headers_with_lang(Some("es-ES,es;q=0.9,en;q=0.8")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(
            location(resp),
            "https://news--digest-seanfloyd-dev.translate.goog/2026-07-03?_x_tr_sl=en&_x_tr_tl=es-ES&_x_tr_hl=es-ES"
        );
    }

    #[tokio::test]
    async fn english_reader_lands_on_default_language() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
            State(state),
            headers_with_lang(Some("en-US,en;q=0.9")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(
            location(resp),
            "https://example-com.translate.goog/2026-07-03?_x_tr_sl=en&_x_tr_tl=fr&_x_tr_hl=fr"
        );
    }

    #[tokio::test]
    async fn never_emits_tl_en() {
        let state = state_with_digest("2026-07-03", Some("example.com"));
        let resp = translate_redirect(
            Path("2026-07-03".to_string()),
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
            State(state),
            headers_with_lang(Some("fr")),
        )
        .await
        .expect("expected redirect")
        .into_response();

        assert_eq!(location(resp), "/2026-07-03");
    }
}
