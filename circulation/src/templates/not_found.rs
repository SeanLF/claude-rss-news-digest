//! Friendly 404 page — shared by the router fallback (unknown path) and the
//! digest handler (valid-format date with no stored issue). Same chrome frame as
//! every other surface; the body is a short apology + a few high-signal ways back
//! (Archive, Today, Search) so a dead link never dead-ends the reader.

use super::chrome;
use super::digest::og_image_tags;

pub struct NotFoundParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    /// Page heading — the variant's one-line "what went wrong".
    pub heading: &'a str,
    /// A sentence of context under the heading.
    pub message: &'a str,
    /// Stable routes offered as the ways back.
    pub today_url: &'a str,
    pub search_url: &'a str,
}

const NOT_FOUND_CSS: &str = r#"
.narrow{max-width:560px;}
.nf-code{font-family:var(--sans); font-weight:700; font-size:13px; letter-spacing:.18em;
  text-transform:uppercase; color:var(--accent-ink); margin:24px 0 0;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:14px 0 0;}
.body{font-family:var(--serif); font-size:16px; color:var(--muted); line-height:1.65; margin:14px 0 0;}
.ways{margin-top:30px; display:flex; gap:12px; flex-wrap:wrap;}
.ways a{font-family:var(--sans); font-size:14px; text-decoration:none; display:inline-flex;
  align-items:center; gap:8px; border:1px solid var(--line); border-radius:8px; padding:11px 18px;
  color:var(--ink2); transition:border-color .15s ease,color .15s ease;}
.ways a:hover{border-color:var(--accent); color:var(--accent-ink);}
.ways a.primary{border-color:var(--accent); color:var(--accent-ink);}
.ways a:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
@media (max-width:560px){ .ways a{width:100%; justify-content:center;} }
"#;

pub fn render_not_found(p: &NotFoundParams) -> String {
    let head = chrome::page_head(
        p.title,
        p.message,
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        NOT_FOUND_CSS,
    );

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <main id="main" class="narrow">
      <a class="brandmark" href="{home}">{brand}</a>
      <p class="nf-code">404</p>
      <h1 class="h1">{heading}</h1>
      <p class="lede">{message}</p>
      <p class="body">The link may be old, or the page may have moved. Here's the way back:</p>
      <div class="ways">
        <a class="primary" href="{home}">&larr; The archive</a>
        <a href="{today}">Today's issue</a>
        <a href="{search}">Search</a>
      </div>
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        home = p.home_url,
        brand = p.brand_html,
        heading = p.heading,
        message = p.message,
        today = p.today_url,
        search = p.search_url,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> NotFoundParams<'static> {
        NotFoundParams {
            title: "News Digest",
            brand_html: "News <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://example.com/nonesuch",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            heading: "Page not found",
            message: "There's nothing at this address.",
            today_url: "/today",
            search_url: "/search",
        }
    }

    #[test]
    fn renders_heading_message_and_ways_back() {
        let html = render_not_found(&params());
        assert!(html.contains("Page not found"));
        assert!(html.contains("There's nothing at this address."));
        // Every way back is present and points at a stable route.
        assert!(html.contains(r#"href="/">&larr; The archive"#));
        assert!(html.contains(r#"href="/today""#));
        assert!(html.contains(r#"href="/search""#));
        assert_eq!(html.matches("<h1").count(), 1);
        assert!(html.contains(r#"<main id="main" class="narrow">"#));
    }
}
