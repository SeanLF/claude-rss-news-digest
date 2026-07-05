//! Sources page — the catalog as a bias spectrum + factuality meters, grouped by lean.
//! Shared frame (top bar, footer, masthead, toggle) comes from [`super::chrome`]; this module owns
//! the spectrum bar, the neutral factuality meter, and the source list.

use super::chrome;
use super::digest::og_image_tags;
use crate::util::escape_html;

/// A single source entry for rendering (already deduped across multi-feed outlets by the handler).
pub struct Source {
    pub name: String,
    pub website: String,
    pub bias: String,
    pub factuality: String,
    pub perspective: String,
    pub feed_count: u32,
}

pub struct SourcesParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub sources: &'a [Source],
}

/// Page-specific CSS (method blurb, spectrum bar, section headers, source list, factuality meter).
/// Bias colours route through the `--bias-*` tokens; the factuality meter is deliberately neutral
/// (`--ink2`/`--line-strong`) — one colour system (bias) per screen.
const SOURCES_CSS: &str = r#"
.method{font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.6; margin:20px 0 0;}
.method a{text-decoration:none; background-image:linear-gradient(var(--accent-ink),var(--accent-ink)); background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.spectrum{margin:28px 0 8px;}
.spec-counts, .spec-labels{display:grid; grid-template-columns:repeat(7,1fr);}
.spec-counts span{text-align:center; font-family:var(--serif); font-weight:600; font-size:19px; color:var(--ink);
  font-variant-numeric:tabular-nums; line-height:1; padding-bottom:6px;}
.spec-bar2{display:grid; grid-template-columns:repeat(7,1fr); height:36px; border-radius:6px; overflow:hidden; border:1px solid var(--line);}
.spec-bar2 .z{display:block;}
.spec-bar2 .e-l{background:color-mix(in srgb, var(--bias-l) 13%, var(--paper));}
.spec-bar2 .e-r{background:color-mix(in srgb, var(--bias-r) 13%, var(--paper));}
.spec-bar2 .on-l{background:var(--bias-l);} .spec-bar2 .on-c{background:var(--bias-c);} .spec-bar2 .on-r{background:var(--bias-r);}
.spec-labels{margin-top:8px;}
.spec-labels span, .spec-labels a{text-align:center; font-family:var(--mono); font-size:9px; letter-spacing:.06em;
  text-transform:uppercase; color:var(--muted); text-decoration:none; padding:0 2px; line-height:1.3;}
.spec-labels a.on{color:var(--ink2);}
.spec-labels a.on:hover{color:var(--accent-ink);}
.spec-cap{font-family:var(--sans); font-size:12px; color:var(--muted); margin:14px 0 0;}
.sec{margin-top:44px;}
.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}
.sec-h .dot{width:9px; height:9px; border-radius:999px;}
.sec-h .dot.l{background:var(--bias-l);} .sec-h .dot.c{background:var(--bias-c);} .sec-h .dot.r{background:var(--bias-r);}
.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}
.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}
.colhead{display:grid; grid-template-columns:1fr auto; gap:16px; padding:10px 8px 6px;
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);}
.colhead .r{text-align:right;}
ul.srcs{list-style:none; margin:0; padding:0;}
ul.srcs li{border-top:1px solid var(--line);}
ul.srcs li a.row{display:grid; grid-template-columns:1fr auto; gap:16px; align-items:center; padding:13px 8px;
  text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
ul.srcs li a.row:hover{background:var(--wash);}
ul.srcs li a.row:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.nm{display:block; font-family:var(--serif); font-size:17px; font-weight:600; color:var(--ink); letter-spacing:-.004em;}
a.row:hover .nm{color:var(--accent-ink);}
.nm .feeds{font-family:var(--mono); font-size:11px; font-weight:400; color:var(--muted); letter-spacing:0;}
.persp{display:block; font-family:var(--sans); font-size:12.5px; color:var(--muted); margin-top:3px;}
.fact{display:inline-flex; align-items:center; gap:9px; white-space:nowrap;}
.meter{display:inline-flex; gap:2px; align-items:flex-end; height:12px;}
.meter i{display:block; width:3px; background:var(--ink2);}
.meter i:nth-child(1){height:6px;} .meter i:nth-child(2){height:9px;} .meter i:nth-child(3){height:12px;}
.meter i.off{background:var(--line-strong);}
.fact .fl{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink2);}
a.row:hover .fact .fl{color:var(--accent-ink);}
"#;

/// Bias string -> bucket char. Mirrors `archive::bias_map`'s collapse (extremes fold to lean-*).
fn bucket(bias: &str) -> char {
    match bias {
        "far-left" | "left" | "lean-left" => 'l',
        "lean-right" | "right" | "far-right" => 'r',
        _ => 'c',
    }
}

/// Factuality rating -> (filled ascending bars, display label). Neutral meter, never colour.
fn factuality_meter(factuality: &str) -> String {
    let (on, label): (usize, &str) = match factuality {
        "very-high" => (3, "Very high"),
        "high" => (2, "High"),
        "mixed" => (1, "Mixed"),
        "low" => (1, "Low"),
        "very-low" => (0, "Very low"),
        _ => (0, "Unrated"),
    };
    let bars: String = (1..=3)
        .map(|i| {
            if i <= on {
                "<i></i>"
            } else {
                r#"<i class="off"></i>"#
            }
        })
        .collect();
    format!(
        r#"<span class="fact"><span class="meter">{bars}</span><span class="fl">{label}</span></span>"#
    )
}

/// One source `<li>` row: name (+ feed count when >1) · perspective · factuality meter.
fn source_row(s: &Source) -> String {
    let feeds = if s.feed_count > 1 {
        format!(
            r#"<span class="feeds"> &middot; {} feeds</span>"#,
            s.feed_count
        )
    } else {
        String::new()
    };
    format!(
        r#"<li><a class="row" href="{url}"><span><span class="nm">{name}{feeds}</span><span class="persp">{persp}</span></span>{meter}</a></li>"#,
        url = escape_html(&s.website),
        name = escape_html(&s.name),
        persp = escape_html(&s.perspective),
        meter = factuality_meter(&s.factuality),
    )
}

/// One bias section (`lean-left` / `centre` / `lean-right`), or "" when the bucket is empty.
/// `sources` is the pre-grouped slice for this bucket; `key` selects the dot colour class.
fn bias_section(sources: &[&Source], key: char, anchor: &str, label: &str) -> String {
    if sources.is_empty() {
        return String::new();
    }
    let n = sources.len();
    let rows: String = sources.iter().copied().map(source_row).collect();
    format!(
        r#"<section class="sec" id="{anchor}"><div class="sec-h"><span class="dot {key}" aria-hidden="true"></span><h2>{label}</h2><span class="ct">{n} outlets</span></div><div class="colhead"><span>Source</span><span class="r">Factuality</span></div><ul class="srcs">{rows}</ul></section>"#
    )
}

/// Render the sources page.
pub fn render_sources(p: &SourcesParams) -> String {
    // Group once by bucket (input is pre-sorted, so each group stays alphabetical).
    let mut left: Vec<&Source> = Vec::new();
    let mut centre: Vec<&Source> = Vec::new();
    let mut right: Vec<&Source> = Vec::new();
    for s in p.sources {
        match bucket(&s.bias) {
            'l' => left.push(s),
            'r' => right.push(s),
            _ => centre.push(s),
        }
    }
    let (ll, c, lr) = (left.len(), centre.len(), right.len());
    let outlets = p.sources.len();
    let feeds: u32 = p.sources.iter().map(|s| s.feed_count).sum();

    let head = chrome::page_head(
        p.title,
        "News sources by bias and factuality — the catalog behind the digest.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        SOURCES_CSS,
    );
    let masthead = chrome::sub_masthead(
        p.home_url,
        p.brand_html,
        "Sources",
        "Bias &amp; factuality &middot; aggregated from Ground News",
        &format!(
            "<b>{outlets}</b> outlets &middot; <b>{feeds}</b> feeds &middot; across the spectrum"
        ),
    );

    // Spectrum: fixed 7 columns (far-left … far-right); only lean-left/centre/lean-right populate.
    // The aria-label + caption state the extremes are empty — true because the catalog is bounded by
    // a factuality floor to {lean-left, center, lean-right} on purpose (see design-system.md). If an
    // extreme-bias outlet is ever added, `bucket()` folds it into lean-*; update this copy then.
    let spec_label = |n: usize, anchor: &str, label: &str| {
        if n > 0 {
            format!(r##"<a class="on" href="#{anchor}">{label}</a>"##)
        } else {
            format!(r#"<span>{label}</span>"#)
        }
    };
    let spectrum = format!(
        r#"<div class="spectrum">
      <div class="spec-counts" aria-hidden="true"><span></span><span></span><span>{ll}</span><span>{c}</span><span>{lr}</span><span></span><span></span></div>
      <div class="spec-bar2" role="img" aria-label="Bias distribution: {ll} lean-left, {c} centre, {lr} lean-right outlets; none far-left, left, right, or far-right">
        <span class="z e-l"></span><span class="z e-l"></span><span class="z on-l"></span><span class="z on-c"></span><span class="z on-r"></span><span class="z e-r"></span><span class="z e-r"></span>
      </div>
      <div class="spec-labels"><span>Far left</span><span>Left</span>{lab_l}{lab_c}{lab_r}<span>Right</span><span>Far right</span></div>
      <p class="spec-cap"><b style="color:var(--ink2);font-weight:600;">{outlets} outlets</b> across the spectrum &mdash; none at the far-left or far-right extremes.</p>
    </div>"#,
        lab_l = spec_label(ll, "lean-left", "Lean left"),
        lab_c = spec_label(c, "centre", "Centre"),
        lab_r = spec_label(lr, "lean-right", "Lean right"),
    );

    let sections = format!(
        "{}{}{}",
        bias_section(&left, 'l', "lean-left", "Lean left"),
        bias_section(&centre, 'c', "centre", "Centre"),
        bias_section(&right, 'r', "lean-right", "Lean right"),
    );

    let method = r#"<p class="method">Ratings aggregate <a href="https://ground.news">Ground News</a>, which combines <a href="https://www.allsides.com">AllSides</a>, <a href="https://adfontesmedia.com">Ad Fontes Media</a>, and <a href="https://mediabiasfactcheck.com">Media Bias/Fact Check</a>. The digest draws from across the political spectrum to show how outlets cover the same story differently. Perspective notes each outlet's vantage point.</p>"#;

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    {masthead}
    <main id="main">
    {method}
    {spectrum}
    {sections}
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn src(name: &str, bias: &str, fact: &str, feeds: u32) -> Source {
        Source {
            name: name.into(),
            website: format!("https://{}.example", name.to_lowercase()),
            bias: bias.into(),
            factuality: fact.into(),
            perspective: "American".into(),
            feed_count: feeds,
        }
    }

    fn params<'a>(sources: &'a [Source]) -> SourcesParams<'a> {
        SourcesParams {
            title: "Sources",
            brand_html: "News <em>Digest</em>",
            home_url: "https://example.com",
            canonical_url: "https://example.com/sources",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            sources,
        }
    }

    #[test]
    fn groups_by_bias_and_counts_the_spectrum() {
        let s = [
            src("Guardian", "lean-left", "high", 1),
            src("Reuters", "center", "very-high", 4),
            src("WSJ", "lean-right", "high", 1),
            src("NPR", "lean-left", "very-high", 2),
        ];
        let html = render_sources(&params(&s));
        // spectrum counts land in the middle three columns
        assert!(html.contains(r#"<span>2</span><span>1</span><span>1</span>"#)); // ll=2, c=1, lr=1
        assert!(
            html.contains(r#"aria-label="Bias distribution: 2 lean-left, 1 centre, 1 lean-right"#)
        );
        // three populated sections with anchors
        assert!(html.contains(r#"<section class="sec" id="lean-left">"#));
        assert!(html.contains(r#"<section class="sec" id="centre">"#));
        assert!(html.contains(r#"<section class="sec" id="lean-right">"#));
        // masthead stat: 4 outlets, 8 feeds
        assert!(html.contains("<b>4</b> outlets &middot; <b>8</b> feeds"));
    }

    #[test]
    fn factuality_meter_maps_ratings_to_bars() {
        assert!(
            factuality_meter("very-high")
                .contains("<i></i><i></i><i></i></span><span class=\"fl\">Very high")
        );
        assert!(factuality_meter("high").contains(r#"<i></i><i></i><i class="off"></i>"#));
        assert!(
            factuality_meter("mixed").contains(r#"<i></i><i class="off"></i><i class="off"></i>"#)
        );
        assert!(factuality_meter("unrated").contains("Unrated"));
    }

    #[test]
    fn feed_count_shown_only_for_multi_feed_outlets() {
        let multi = [src("Reuters", "center", "very-high", 4)];
        assert!(render_sources(&params(&multi)).contains("&middot; 4 feeds"));
        let single = [src("BBC", "center", "high", 1)];
        assert!(!render_sources(&params(&single)).contains("feeds</span>"));
    }

    #[test]
    fn escapes_source_names() {
        let s = [src("A & <B>", "center", "high", 1)];
        let html = render_sources(&params(&s));
        assert!(html.contains("A &amp; &lt;B&gt;"));
    }
}
