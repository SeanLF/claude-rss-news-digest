//! Shared static-asset plumbing: the canonical token CSS (compiled in from the single
//! `design/tokens.css` source) and the vendored Source Serif 4 woff2, plus the helpers
//! that inline the tokens + `@font-face` into each page's `<style>` at render time.
//!
//! Delivery model (design-system.md "Token source of truth + production delivery"):
//! DRY at the source (one `tokens.css`, shared with newsroom), inline at the output
//! (per-page `<style>` — no render-blocking external sheet, no unused-CSS flag, best
//! Lighthouse). Fonts are the one cache-busted asset: served at a content-hashed path
//! with an immutable year-long cache. This collapses the six duplicated `:root` blocks
//! that used to live inline in each template.

use axum::http::StatusCode;
use axum::response::IntoResponse;

/// Canonical design tokens — the single source of truth, `include_str!`'d from the repo-root
/// `design/tokens.css` (the same file newsroom reads). Follows the `sources.json` precedent.
pub const TOKENS_CSS: &str = include_str!("../../design/tokens.css");

/// Vendored variable woff2 (Source Serif 4, weights 380–640, latin subset). Served as bytes,
/// never base64-inlined in production (the mockups inline it only for self-containment).
pub const FONT_WOFF2: &[u8] = include_bytes!("../assets/fonts/source-serif-4-latin.woff2");

/// 8-hex content fingerprint of the font bytes (FNV-1a, folded to 32 bits). This is a
/// cache-busting fingerprint, not a security hash, so a dependency-free hash is the right
/// weight — any byte change flips it with overwhelming probability. Computed once at startup.
pub fn font_hash() -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325; // FNV-1a 64-bit offset basis
    for &b in FONT_WOFF2 {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3); // FNV-1a 64-bit prime
    }
    format!("{:08x}", (h ^ (h >> 32)) as u32)
}

/// Content-addressed font URL, e.g. `/assets/fonts/source-serif-4.2a24bad4.woff2`.
pub fn font_url(hash: &str) -> String {
    format!("/assets/fonts/source-serif-4.{hash}.woff2")
}

/// `@font-face` binding the family name to the hashed URL. `font-display:swap` avoids FOIT
/// (Georgia shows immediately, swaps to Source Serif 4 once loaded). Newsroom's CSS only ever
/// references the family name (`--serif`), so it needs no knowledge of this URL.
pub fn font_face(font_url: &str) -> String {
    format!(
        r#"@font-face{{font-family:"Source Serif 4";font-style:normal;font-weight:380 640;font-display:swap;src:url("{font_url}") format("woff2");}}"#
    )
}

/// Compose a page's inline `<style>`: the `@font-face`, then the shared tokens, then the page's
/// own CSS. One inlined critical sheet per page — the DRY-source / inlined-output model.
pub fn head_style(page_css: &str, font_url: &str) -> String {
    format!(
        "<style>{ff}{tokens}{page}</style>",
        ff = font_face(font_url),
        tokens = TOKENS_CSS,
        page = page_css,
    )
}

/// `GET /assets/fonts/source-serif-4.{hash}.woff2` — the vendored font. The path carries the
/// content hash (built at startup), so the response is safe to cache immutably for a year.
pub async fn font() -> impl IntoResponse {
    (
        StatusCode::OK,
        [
            ("content-type", "font/woff2"),
            ("cache-control", "public, max-age=31536000, immutable"),
        ],
        FONT_WOFF2,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_css_is_the_canonical_source() {
        // Sanity: the compiled-in CSS is the real tokens file, not an empty/placeholder include.
        assert!(TOKENS_CSS.contains("--accent:    #b1352a"));
        assert!(TOKENS_CSS.contains("--ink:       #191917"));
        assert!(TOKENS_CSS.contains(r#"--serif:"Source Serif 4""#));
        // Both explicit-toggle blocks are present (no-flash toggle relies on them).
        assert!(TOKENS_CSS.contains(r#":root[data-theme="dark"]"#));
        assert!(TOKENS_CSS.contains(r#":root[data-theme="light"]"#));
    }

    #[test]
    fn font_bytes_are_a_real_woff2() {
        // wOF2 magic number — guards against a truncated/wrong include.
        assert_eq!(&FONT_WOFF2[0..4], b"wOF2");
        assert!(FONT_WOFF2.len() > 50_000);
    }

    #[test]
    fn font_hash_is_stable_8_hex_and_content_addressed() {
        let h = font_hash();
        assert_eq!(h.len(), 8);
        assert!(h.chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(h, font_hash(), "hash must be deterministic across calls");
    }

    #[test]
    fn font_url_and_face_agree_on_the_hashed_path() {
        let h = font_hash();
        let url = font_url(&h);
        assert_eq!(url, format!("/assets/fonts/source-serif-4.{h}.woff2"));
        let ff = font_face(&url);
        assert!(ff.contains(&format!(r#"src:url("{url}") format("woff2")"#)));
        assert!(ff.contains("font-weight:380 640"));
        assert!(ff.contains("font-display:swap"));
    }

    #[test]
    fn head_style_inlines_font_face_then_tokens_then_page_css() {
        let out = head_style(".foo{color:var(--ink)}", "/assets/fonts/x.woff2");
        assert!(out.starts_with("<style>@font-face"));
        assert!(out.ends_with(".foo{color:var(--ink)}</style>"));
        // tokens sit between the face and the page CSS
        let face_at = out.find("@font-face").unwrap();
        let tokens_at = out.find("--accent").unwrap();
        let page_at = out.find(".foo").unwrap();
        assert!(face_at < tokens_at && tokens_at < page_at);
    }
}
