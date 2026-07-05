//! Digest page template - navigation and web-specific elements injected into stored HTML.

use super::chrome::{TOGGLE_BTN, topbar as chrome_topbar};
use crate::routes;

/// Favicon as inline SVG data URI (terracotta document icon)
pub const FAVICON_SVG: &str = r#"<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23c45a3b'/%3E%3Cline x1='8' y1='10' x2='24' y2='10' stroke='white' stroke-width='2.5' stroke-linecap='round'/%3E%3Cline x1='8' y1='16' x2='20' y2='16' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.7'/%3E%3Cline x1='8' y1='22' x2='16' y2='22' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.4'/%3E%3C/svg%3E">"#;

/// Skip-to-content link CSS -- shared across all pages.
pub const SKIP_LINK_CSS: &str = r#"
.skip-link {
    position: absolute;
    left: -9999px;
    top: auto;
    width: 1px;
    height: 1px;
    overflow: hidden;
    z-index: 1000;
    padding: 0.75rem 1.5rem;
    background: var(--bg, #fafaf8);
    color: var(--ruby, #c45a3b);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 0.9rem;
    text-decoration: none;
    border: 2px solid var(--ruby, #c45a3b);
    border-radius: 4px;
}
.skip-link:focus-visible {
    position: fixed;
    left: 1rem;
    top: 1rem;
    width: auto;
    height: auto;
    overflow: visible;
}"#;

/// Reduced-motion support CSS -- shared across all pages.
pub const REDUCED_MOTION_CSS: &str = r#"
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
    }
    html { scroll-behavior: auto; }
}"#;

/// Skip-to-content link HTML -- first child of <body> on every page.
pub const SKIP_LINK_HTML: &str = r##"<a href="#main" class="skip-link">Skip to content</a>"##;

/// CSS injected into digest pages for the web archive: the shared top utility bar
/// (nav + translate pill + theme toggle), the email->web visibility flip, and the
/// web-only feedback line. The digest keeps its OWN document frame (masthead, body,
/// footer) from `digest.css`, so this deliberately does NOT pull in the app
/// `CHROME_CSS` (which would collide on `.masthead`/`.brand`/`body`/`a`). It styles
/// only the injected chrome, using the digest's own tokens (`--hair`, not chrome's
/// `--line`) so it matches the digest paper across every stored issue, light + dark.
/// Ported from the web-edition block of `scratch/chrome-mockups/digest_v1.html`.
pub const DIGEST_NAV_CSS: &str = r#"<style>
/* pull the paper's top padding in now that a utility bar sits above the masthead */
.paper{padding-top:28px;}

/* top utility bar */
.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:30px;}
.topnav{font-family:var(--sans);font-size:13px;line-height:normal;display:flex;align-items:center;flex-wrap:nowrap;}
.topnav a{color:var(--muted);text-decoration:none;}
.topnav a:hover{color:var(--accent-ink);}
.topnav a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.topnav .sep{color:var(--hair);padding:0 10px;}
.topright{display:flex;align-items:center;gap:12px;}

/* translate pill -- a quiet accent-outlined chip (matches the chrome surfaces' `.pill`) */
.pill{display:inline-flex;align-items:center;gap:6px;font-family:var(--sans);font-size:12px;
  color:var(--accent-ink);text-decoration:none;line-height:normal;
  border:1px solid var(--accent);border-radius:999px;padding:5px 12px;
  transition:background .15s ease,border-color .15s ease;}
.pill .g{font-family:var(--serif);}
.pill:hover{background:color-mix(in srgb,var(--accent) 12%,var(--bg));}
.pill:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}

/* theme toggle -- shared TOGGLE_BTN markup: two stacked glyphs crossfade on hover
   (current -> next-state), a visual echo of its "Switch to {next}" title. */
.toggle{font-family:var(--sans);font-size:12px;color:var(--muted);background:none;
  border:1px solid var(--hair);border-radius:6px;padding:6px 10px;cursor:pointer;line-height:normal;
  display:inline-flex;align-items:center;gap:6px;min-height:24px;}
.toggle:hover{color:var(--accent-ink);border-color:var(--accent);}
.toggle:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.tglyphs{position:relative;display:inline-flex;flex:none;width:1em;height:1em;font-size:13px;line-height:1;}
.tglyph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;line-height:1;
  transition:opacity .2s ease,transform .2s ease;}
.tg-next{opacity:0;transform:translateY(35%);}
.toggle:hover .tg-cur{opacity:0;transform:translateY(-35%);}
.toggle:hover .tg-next{opacity:1;transform:translateY(0);}

/* Hide the pill once the reader is on Google's translate.goog proxy -- redundant
   there (Google's own language bar is the backstop). Two signals: Google's own
   translated-* class on <html>, and the via-proxy hostname flag (head script). */
html.translated-ltr .pill,
html.translated-rtl .pill,
.via-proxy .pill {
    display: none;
}

/* Feedback invitation -- its own line, clearly clickable, so it doesn't read as
   just another footer link. */
.footer-feedback {
    margin: 12px 0 4px;
    color: var(--muted);
}
.footer-feedback a {
    color: var(--accent-ink);
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 2px;
}

/* email->web visibility flip: digest.css defaults to the EMAIL state
   (`.web-only{display:none}`); the web archive reverses it. */
.email-only{display:none;}
.web-only{display:block;}
/* footer-nav Subscribe is web-only (redundant in the email, which has Unsubscribe);
   keep it inline in the nav row rather than the default block flip. */
footer nav .web-only{display:inline;}

@media (max-width:420px){.tword{display:none;}.toggle{padding:6px 9px;}}
</style>"#;

/// Injected into the digest `<head>`: when the page is being viewed through
/// Google's `translate.goog` proxy, flag the document (`via-proxy`) so the
/// on-page Translate control hides itself. The reader is already translated and
/// Google's own language bar is the backstop (spec §2); showing our pill there is
/// redundant and would re-translate. Runs in `<head>` to avoid a flash. Belt and
/// suspenders with the `html.translated-*` CSS rule (Google's own marker).
pub const PROXY_TRANSLATE_HIDE_SCRIPT: &str = r#"<script>if(location.hostname.indexOf("translate.goog")>-1)document.documentElement.className+=" via-proxy";</script>"#;

/// The shared web top bar injected above the digest masthead. Matches the other
/// chrome surfaces (index/sources/threads/stats): the `← Archive · Sources ·
/// Threads · Stats` nav on the left, and a right cluster of the `文A Translate`
/// pill (spec §3.2, links to this date's `/{date}/translate` -- engine-agnostic,
/// Google self-brands on the destination) plus the shared `TOGGLE_BTN` theme
/// toggle. Built from `chrome::topbar` so the frame is identical to every other
/// surface; the digest supplies its own topbar CSS via [`DIGEST_NAV_CSS`] rather
/// than the app `CHROME_CSS` (which would collide with the digest's own frame).
pub fn digest_nav_html(date: &str) -> String {
    let nav: &[(&str, &str)] = &[
        ("/", "&larr; Archive"),
        (routes::SOURCES, "Sources"),
        (routes::THREADS, "Threads"),
        (routes::STATS, "Stats"),
    ];
    let right = format!(
        r#"<a class="pill" href="/{date}/translate"><span class="g" aria-hidden="true">文A</span> Translate</a>{TOGGLE_BTN}"#
    );
    chrome_topbar(nav, &right)
}

/// Web-only feedback line folded into the digest's OWN footer (the footer keeps
/// its Subscribe link and the flip hides the email-only Unsubscribe, so the old
/// unsubscribe->subscribe swap is now structural -- only the feedback affordance
/// remains to inject). When `feedback_email` is set, returns a clean invitation
/// line with a `mailto:` (spec §4), the digest date prefilled in the subject;
/// otherwise the empty string (nothing injected).
pub fn web_feedback_html(date: &str, feedback_email: Option<&str>) -> String {
    // date is a validated YYYY-MM-DD and feedback_email is trusted config
    // (RESEND_FROM), so the mailto is safe to build without extra encoding. Its own
    // invitation line (not a middot footer link) so the feedback channel is obvious
    // -- the web parallel to the email's "just hit reply".
    feedback_email
        .map(|email| {
            format!(
                r#"<p class="footer-feedback web-only">Got feedback or a suggestion? <a href="mailto:{email}?subject=Digest%20feedback%20-%20{date}">Send a note &rarr;</a></p>"#
            )
        })
        .unwrap_or_default()
}

/// Build OG meta tags for a digest page
pub fn digest_og_tags(
    title: &str,
    description: &str,
    canonical_url: &str,
    site_name: &str,
    image_url: &str,
) -> String {
    format!(
        r#"<meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{canonical_url}">
  <meta property="og:site_name" content="{site_name}">
  <meta name="description" content="{description}">
  {image_tags}"#,
        image_tags = og_image_tags(image_url)
    )
}

/// Build og:image / twitter:card meta tags shared by every page with OG tags.
pub fn og_image_tags(image_url: &str) -> String {
    format!(
        r#"<meta property="og:image" content="{image_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">"#
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_og_tags_includes_absolute_image_and_twitter_card() {
        let html = digest_og_tags(
            "Title",
            "Description",
            "https://example.com/2026-07-01",
            "News Digest",
            "https://example.com/og-image.png",
        );
        assert!(
            html.contains(
                r#"<meta property="og:image" content="https://example.com/og-image.png">"#
            )
        );
        assert!(html.contains(r#"<meta property="og:image:width" content="1200">"#));
        assert!(html.contains(r#"<meta property="og:image:height" content="630">"#));
        assert!(html.contains(r#"<meta name="twitter:card" content="summary_large_image">"#));
    }

    #[test]
    fn og_image_tags_uses_the_given_absolute_url() {
        let html = og_image_tags("https://example.com/og-image.png");
        assert!(html.contains(r#"content="https://example.com/og-image.png""#));
    }

    #[test]
    fn digest_nav_is_the_shared_topbar_with_the_dates_translate_pill() {
        let html = digest_nav_html("2026-07-03");
        // The shared chrome frame -- identical structure to the other surfaces.
        assert!(
            html.contains(r#"<div class="topbar">"#),
            "digest web nav must be the shared top bar: {html}"
        );
        assert!(html.contains(r#"<nav class="topnav""#));
        // Left nav: Archive · Sources · Threads · Stats (matches the mockup).
        assert!(html.contains("Archive"));
        assert!(html.contains(r#"href="/sources""#));
        assert!(html.contains(r#"href="/threads""#));
        assert!(html.contains(r#"href="/stats""#));
        // Right cluster: the 文A Translate pill for THIS date, then the theme toggle.
        assert!(
            html.contains(r#"class="pill" href="/2026-07-03/translate""#),
            "translate is a pill linking the date's translate route: {html}"
        );
        assert!(html.contains("文"), "nav should carry the 文A glyph");
        assert!(
            html.contains(r#"id="themeBtn""#),
            "right cluster carries the shared theme toggle: {html}"
        );
        // Pill precedes the toggle in the right cluster.
        let pill = html.find("class=\"pill\"").unwrap();
        let toggle = html.find("id=\"themeBtn\"").unwrap();
        assert!(pill < toggle, "translate pill precedes the theme toggle");
    }

    #[test]
    fn web_feedback_adds_mailto_line_when_address_configured() {
        let html = web_feedback_html("2026-07-03", Some("daily-news-digest@seanfloyd.dev"));
        // A dedicated web-only invitation line with a clear call-to-action and the
        // date prefilled in the subject.
        assert!(
            html.contains(r#"class="footer-feedback web-only"#),
            "own web-only line: {html}"
        );
        assert!(html.contains("feedback or a suggestion"));
        assert!(html.contains(r#"mailto:daily-news-digest@seanfloyd.dev?subject="#));
        assert!(html.contains("2026-07-03"));
    }

    #[test]
    fn translate_pill_hides_when_viewed_through_the_google_proxy() {
        // Two independent signals so the now-redundant pill goes away once the
        // reader is on translate.goog: Google's own translated-* class on <html>,
        // and a hostname flag set by the injected head script.
        assert!(
            DIGEST_NAV_CSS.contains("html.translated-ltr .pill"),
            "should hide on Google's translated-ltr marker"
        );
        assert!(DIGEST_NAV_CSS.contains(".via-proxy .pill"));
        assert!(
            PROXY_TRANSLATE_HIDE_SCRIPT.contains("translate.goog"),
            "head script must sniff the proxy hostname"
        );
        assert!(PROXY_TRANSLATE_HIDE_SCRIPT.contains("via-proxy"));
    }

    #[test]
    fn web_feedback_omits_line_when_no_address() {
        let html = web_feedback_html("2026-07-03", None);
        assert!(
            !html.contains("mailto:"),
            "no address -> no feedback link: {html}"
        );
    }

    #[test]
    fn digest_nav_css_flips_email_web_visibility_for_the_archive() {
        // digest.css defaults to the EMAIL state; the injected chrome must reverse it.
        assert!(DIGEST_NAV_CSS.contains(".email-only{display:none;}"));
        assert!(DIGEST_NAV_CSS.contains(".web-only{display:block;}"));
    }
}
