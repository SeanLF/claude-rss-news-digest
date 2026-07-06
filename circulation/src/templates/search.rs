//! Search page — full-text results over shown headlines, in the chrome design. Shared frame comes
//! from [`super::chrome`]; this module owns the search field, the result rows, and the empty states.

use super::chrome;
use super::digest::og_image_tags;
use crate::search::SearchResult;
use crate::util::{escape_html, format_day_month_year};

pub struct SearchParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    /// Form action (the `/search` route).
    pub search_url: &'a str,
    /// `None` = the landing state (no `q`); the handler collapses blank queries to `None`.
    pub query: Option<&'a str>,
    pub results: &'a [SearchResult],
}

const SEARCH_CSS: &str = r#"
.searchform{display:flex; gap:10px; align-items:center; margin-top:16px;}
.searchfield{flex:1; min-width:0; font-family:var(--sans); font-size:16px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line-strong); border-radius:var(--r-input); padding:11px 14px;}
.searchfield::placeholder{color:var(--muted);}
.searchfield:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}
.searchbtn{font-family:var(--sans); font-size:13px; font-weight:600; color:#fff; background:var(--accent-ink);
  border:1px solid var(--accent-ink); border-radius:var(--r-input); padding:11px 18px; cursor:pointer; white-space:nowrap;}
@media (prefers-color-scheme:dark){ .searchbtn{background:var(--accent); color:#16150f; border-color:var(--accent);} }
:root[data-theme="dark"]  .searchbtn{background:var(--accent); color:#16150f; border-color:var(--accent);}
:root[data-theme="light"] .searchbtn{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
.rescount{font-family:var(--mono); font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin:24px 0 8px;}
.rescount b{color:var(--ink2); font-weight:600;}
.results{list-style:none; margin:0; padding:0;}
.result{border-top:1px solid var(--line);}
.result a{display:grid; grid-template-columns:120px 1fr; gap:4px 18px; align-items:baseline; padding:16px 0; text-decoration:none; border-radius:6px;}
.result a:hover{background:var(--wash);}
.result a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.result .r-date{font-family:var(--mono); font-size:12px; letter-spacing:.04em; color:var(--muted); white-space:nowrap;}
.result .r-tier{grid-column:1; font-family:var(--mono); font-size:10px; letter-spacing:.1em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-top:2px;}
.result .r-tier.must{color:var(--accent-ink);}
.result .r-head{grid-column:2; grid-row:1 / span 2; font-family:var(--serif); font-size:18px; font-weight:600; line-height:1.3; letter-spacing:-.01em; color:var(--ink); text-wrap:pretty;}
.result a:hover .r-head{color:var(--accent-ink);}
.result.unlinked{display:grid; grid-template-columns:120px 1fr; gap:4px 18px; padding:16px 0;}
.note{font-family:var(--sans); font-size:13px; color:var(--muted); margin-top:24px;}
"#;

/// (tier class, display label) for a tier value; unknown tiers fall back to the escaped raw value.
fn tier_parts(tier: &str) -> (&'static str, String) {
    match tier {
        "must_know" => ("must", "Must Know".to_string()),
        "should_know" => ("should", "Should Know".to_string()),
        "" => ("should", String::new()),
        other => ("should", escape_html(other)),
    }
}

fn result_row(r: &SearchResult) -> String {
    let (tier_cls, label) = tier_parts(&r.tier);
    let head = escape_html(&r.headline);
    match &r.date {
        Some(date) => format!(
            r#"<li class="result"><a href="/issues/{date}"><span class="r-date">{d}</span><span class="r-tier {tier_cls}">{label}</span><span class="r-head">{head}</span></a></li>"#,
            d = format_day_month_year(date),
        ),
        // No digest row to link to (shouldn't happen) — render as plain text, not a dead link.
        None => format!(
            r#"<li class="result unlinked"><span class="r-tier {tier_cls}">{label}</span><span class="r-head">{head}</span></li>"#
        ),
    }
}

/// The results/empty body for the current query state.
fn results_body(query: Option<&str>, results: &[SearchResult]) -> String {
    match query {
        None => {
            r#"<p class="note">Search matches wording in every published headline. Enter a term above.</p>"#.to_string()
        }
        Some(q) if results.is_empty() => format!(
            r#"<p class="rescount">No results for &ldquo;{q}&rdquo;.</p><p class="note">Search matches headline wording — try a broader or differently-worded term.</p>"#,
            q = escape_html(q)
        ),
        Some(q) => {
            let rows: String = results.iter().map(result_row).collect();
            format!(
                r#"<p class="rescount"><b>{n}</b> results for &ldquo;{q}&rdquo;</p><ol class="results">{rows}</ol><p class="note">Results match headlines across every past issue; each opens that day's digest.</p>"#,
                n = results.len(),
                q = escape_html(q),
            )
        }
    }
}

pub fn render_search(p: &SearchParams) -> String {
    let head = chrome::page_head(
        p.title,
        "Search every published headline in the digest archive.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        SEARCH_CSS,
    );
    let value = p.query.map(escape_html).unwrap_or_default();
    let body = results_body(p.query, p.results);

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <header class="masthead">
      <a class="brandmark" href="{home}">{brand}</a>
      <h1 class="h1">Search the archive</h1>
      <form class="searchform" role="search" action="{search}" method="get">
        <input class="searchfield" type="search" name="q" value="{value}" placeholder="Search past headlines&hellip;" aria-label="Search past headlines">
        <button class="searchbtn" type="submit">Search</button>
      </form>
    </header>
    <main id="main">
    {body}
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
        search = p.search_url,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn res(headline: &str, tier: &str, date: Option<&str>) -> SearchResult {
        SearchResult {
            headline: headline.into(),
            tier: tier.into(),
            date: date.map(String::from),
        }
    }

    fn params<'a>(query: Option<&'a str>, results: &'a [SearchResult]) -> SearchParams<'a> {
        SearchParams {
            title: "Search",
            brand_html: "News <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://example.com/search",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            search_url: "/search",
            query,
            results,
        }
    }

    #[test]
    fn landing_state_has_no_results_and_prompts() {
        let html = render_search(&params(None, &[]));
        assert!(html.contains("Enter a term above"));
        assert!(!html.contains(r#"class="results""#));
        assert_eq!(html.matches("<h1").count(), 1);
    }

    #[test]
    fn zero_results_shows_honest_hint() {
        let html = render_search(&params(Some("volcano"), &[]));
        assert!(html.contains(r#"No results for &ldquo;volcano&rdquo;."#));
        assert!(html.contains("try a broader or differently-worded term"));
    }

    #[test]
    fn populated_results_count_tier_and_link() {
        let r = [
            res("Iran talks resume", "must_know", Some("2026-07-01")),
            res("Oil steadies", "should_know", Some("2026-06-17")),
        ];
        let html = render_search(&params(Some("iran"), &r));
        assert!(html.contains(r#"<b>2</b> results for &ldquo;iran&rdquo;"#));
        assert!(html.contains(r#"<a href="/issues/2026-07-01">"#));
        assert!(html.contains(r#"<span class="r-tier must">Must Know</span>"#));
        assert!(html.contains(r#"<span class="r-tier should">Should Know</span>"#));
        assert!(html.contains("1 Jul 2026"));
    }

    #[test]
    fn escapes_headline_and_query() {
        let r = [res("A & <b>", "must_know", Some("2026-07-01"))];
        let html = render_search(&params(Some("<x>"), &r));
        assert!(html.contains("A &amp; &lt;b&gt;"));
        assert!(html.contains("&lt;x&gt;"));
        assert!(!html.contains("<b>A"));
    }

    #[test]
    fn unlinked_result_has_no_dead_link() {
        let r = [res("Orphan headline", "must_know", None)];
        let html = render_search(&params(Some("orphan"), &r));
        // the unlinked row is plain text — it opens with a <span>, never an <a>
        assert!(html.contains(r#"<li class="result unlinked"><span"#));
        assert!(!html.contains(r#"<li class="result unlinked"><a"#));
    }
}
