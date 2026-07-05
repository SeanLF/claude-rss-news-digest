//! Shared app-chrome: the cross-page contract every chrome surface renders identically —
//! the top bar (nav + translate pill + theme toggle), the base frame (`.wrap`/`.col`), the
//! footer, and the 3-state theme toggle (no-flash head script + cycle JS). Keeping this in one
//! place is what guarantees the pixel-parity that `scratch/chrome-mockups/cssdiff.js` checks
//! (`.topbar`, `.topnav`, `.pill`, `#themeBtn`, `.topright`, `footer`). The digest document keeps
//! its own separate frame by design — this module is for the app chrome only.

use crate::assets;

/// Shared chrome CSS: base frame + top bar cluster + footer + toggle. Composed *before* each page's
/// own CSS by [`page_head`], after the `@font-face` + `tokens.css` that [`assets::head_style`] inlines.
/// Plain raw string (not a format template) so the literal `{}` need no escaping.
pub const CHROME_CSS: &str = r#"
*{box-sizing:border-box;}
/* on html so rubber-band overscroll past the top/bottom shows the paper ground, not white */
html{background:var(--paper);}
body{margin:0;}
.wrap{background:var(--paper); color:var(--ink); font-family:var(--serif);
  -webkit-font-smoothing:antialiased; min-height:100vh; padding:28px 32px 72px; hanging-punctuation:first last;}
.col{max-width:820px; margin:0 auto;}
a{color:var(--accent-ink);}
.btn{font-family:var(--sans); font-size:13px; font-weight:600; padding:10px 18px; cursor:pointer;
  border:1px solid; border-radius:var(--r-input); white-space:nowrap;}
.btn.primary{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
@media (prefers-color-scheme:dark){ .btn.primary{background:var(--accent); color:#16150f; border-color:var(--accent);} }
:root[data-theme="dark"] .btn.primary{background:var(--accent); color:#16150f; border-color:var(--accent);}
:root[data-theme="light"] .btn.primary{background:var(--accent-ink); color:#fff; border-color:var(--accent-ink);}
.btn.secondary{background:none; color:var(--accent-ink); border-color:var(--line-strong);}
.btn.secondary:hover{border-color:var(--accent); background:var(--wash);}
.btn:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

/* top utility bar */
.topbar{display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:30px; flex-wrap:wrap;}
.topnav{font-family:var(--sans); font-size:13px; display:flex; align-items:center;}
.topnav a{color:var(--muted); text-decoration:none;}
.topnav a:hover{color:var(--accent-ink);}
.topnav .sep{color:var(--line-strong); padding:0 10px;}
.topright{display:flex; align-items:center; gap:12px; font-family:var(--sans); font-size:12px;}
.topright a.sublink{font-family:var(--sans); font-size:12px; color:var(--muted); text-decoration:none;}
.topright a.sublink:hover{color:var(--accent-ink);}
.topright a.sublink:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
.pill{display:inline-flex; align-items:center; gap:6px; color:var(--accent-ink); border:1px solid var(--accent);
  border-radius:999px; padding:5px 12px; text-decoration:none; line-height:normal;}
.pill .g{font-family:var(--serif);}
.toggle{font-family:var(--sans); font-size:12px; color:var(--muted); background:none; border:1px solid var(--line);
  border-radius:var(--r-input); padding:6px 10px; cursor:pointer; line-height:normal;
  display:inline-flex; align-items:center; gap:6px; min-height:24px;}
.toggle:hover{color:var(--accent-ink); border-color:var(--accent);}
.toggle:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
.tglyph{font-size:13px; line-height:1;}
@media (max-width:420px){ .tword{display:none;} }

/* footer */
.site-foot{margin-top:44px; border-top:1px solid var(--line); padding-top:18px; font-family:var(--sans);
  font-size:12px; color:var(--muted); display:flex; flex-direction:column; gap:6px;}
.site-foot a{color:var(--muted); text-decoration:none; display:inline-block; padding:6px 0;}
.site-foot a:hover{color:var(--accent-ink);}
.site-foot .row{display:flex; flex-wrap:wrap; align-items:center;}
.site-foot .row .sep{color:var(--line-strong); padding:0 8px;}

/* masthead — shared: the index's big brand-as-h1 (.brand) and the sub-pages' eyebrow+title
   (.brandmark + .h1). The .masthead/.sub/.kicker/.stat frame is identical for both. */
.masthead{border-bottom:2px solid var(--ink); padding-bottom:16px;}
.brand{font-family:var(--serif); font-weight:600; font-size:38px; letter-spacing:-.018em; margin:0; line-height:1.02;}
.brand em{color:var(--accent-ink); font-style:normal;}
.brandmark{display:inline-block; font-family:var(--serif); font-size:15px; font-weight:600; color:var(--ink2);
  text-decoration:none; letter-spacing:-.005em; margin-bottom:5px;}
.brandmark em{color:var(--accent-ink); font-style:normal;}
.brandmark:hover{color:var(--ink);}
.h1{font-family:var(--serif); font-weight:600; font-size:34px; letter-spacing:-.018em; margin:0; line-height:1.04;}
.sub{display:flex; align-items:baseline; justify-content:space-between; gap:20px; margin-top:12px;}
.kicker{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);}
.stat{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap;}
.stat b{color:var(--ink2); font-weight:600;}

@media (max-width:560px){ .wrap{padding:22px 18px 56px;} .brand{font-size:29px;} .h1{font-size:27px;} .sub{flex-direction:column; gap:6px;} }

/* skip link — visible only when focused */
.skip{position:absolute; left:-9999px; top:0; z-index:10; background:var(--accent-ink); color:#fff;
  font-family:var(--sans); font-size:13px; padding:8px 14px; border-radius:var(--r-input);}
.skip:focus{left:8px; top:8px;}

@media (prefers-reduced-motion:reduce){ *,*::before,*::after{transition-duration:.01ms !important;} }
"#;

/// Skip-to-content link — first focusable element, targets the page's `<main id="main">`.
pub const SKIP_HTML: &str = r##"<a class="skip" href="#main">Skip to content</a>"##;

/// Favicon: a rounded accent tile (matches the mockups). Data-URI so no extra request.
pub const FAVICON: &str = r##"<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E%3Crect width=%2216%22 height=%2216%22 rx=%222%22 fill=%22%23b1352a%22/%3E%3C/svg%3E">"##;

/// No-flash theme boot: runs synchronously in `<head>` before first paint, applying a stored
/// light/dark preference so there is no flash of the wrong theme. System pref needs no attribute.
pub const NO_FLASH_SCRIPT: &str = "<script>(function(){try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>";

/// The theme-toggle button (glyph + word; word hides ≤420px, `aria-label` set live by the JS).
pub const TOGGLE_BTN: &str = r#"<button class="toggle" id="themeBtn" type="button" aria-label="Theme"><span class="tglyph" aria-hidden="true">◐</span><span class="tword">System</span></button>"#;

/// The 3-state cycle System → Light → Dark → System. localStorage-persisted; live-follows the OS
/// while in system mode. Belongs at end of `<body>`.
pub const TOGGLE_JS: &str = r#"<script>(function(){
  var btn=document.getElementById('themeBtn');if(!btn)return;
  var root=document.documentElement,ORDER=['system','light','dark'],
      ICON={system:'◐',light:'☀',dark:'☾'},WORD={system:'System',light:'Light',dark:'Dark'};
  function cur(){try{var t=localStorage.getItem('theme');return(t==='light'||t==='dark')?t:'system';}catch(e){return'system';}}
  function paint(s){var g=btn.querySelector('.tglyph'),w=btn.querySelector('.tword');
    if(g)g.textContent=ICON[s];if(w)w.textContent=WORD[s];
    var nx=ORDER[(ORDER.indexOf(s)+1)%3];
    btn.setAttribute('aria-label','Theme: '+WORD[s]+'. Activate to switch to '+WORD[nx]+'.');
    btn.setAttribute('title','Switch to '+WORD[nx]);}
  function apply(s){if(s==='system'){root.removeAttribute('data-theme');try{localStorage.removeItem('theme');}catch(e){}}
    else{root.setAttribute('data-theme',s);try{localStorage.setItem('theme',s);}catch(e){}}paint(s);}
  btn.addEventListener('click',function(){apply(ORDER[(ORDER.indexOf(cur())+1)%3]);});
  try{var mq=window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener&&mq.addEventListener('change',function(){if(cur()==='system')paint('system');});}catch(e){}
  paint(cur());
})();</script>"#;

/// One nav row item: a link plus a trailing middot separator when `!last`.
fn nav_item(href: &str, label: &str, last: bool) -> String {
    let sep = if last {
        ""
    } else {
        r#"<span class="sep" aria-hidden="true">&middot;</span>"#
    };
    format!(r#"<a href="{href}">{label}</a>{sep}"#)
}

/// Join `(href,label)` items into a middot-separated link row (shared by the top bar and footer).
fn nav_row(items: &[(&str, &str)]) -> String {
    let mut out = String::new();
    for (i, (href, label)) in items.iter().enumerate() {
        out.push_str(&nav_item(href, label, i + 1 == items.len()));
    }
    out
}

/// Render the shared top bar. `nav` = the left `(href,label)` links (current section omitted per the
/// contract); `right` = the pre-built right cluster (sublinks + translate pill + toggle).
pub fn topbar(nav: &[(&str, &str)], right: &str) -> String {
    let links = nav_row(nav);
    format!(
        r#"<div class="topbar"><nav class="topnav" aria-label="Site navigation">{links}</nav><div class="topright">{right}</div></div>"#
    )
}

/// Render the shared footer: a middot-separated link row + one contextual tagline line.
pub fn footer(links: &[(&str, &str)], tagline: &str) -> String {
    let row = nav_row(links);
    format!(
        r#"<footer class="site-foot"><div class="row">{row}</div><p style="margin:0;">{tagline}</p></footer>"#
    )
}

/// Sub-page masthead: the brand eyebrow (links home) over the page title, then a mono kicker +
/// right-aligned stat sub-row. `brand_html` is the accented brand (e.g. `News <em>Digest</em>`),
/// `stat_html` may contain `<b>` figures.
pub fn sub_masthead(
    home_url: &str,
    brand_html: &str,
    title: &str,
    kicker: &str,
    stat_html: &str,
) -> String {
    format!(
        r#"<header class="masthead"><a class="brandmark" href="{home_url}">{brand_html}</a><h1 class="h1">{title}</h1><div class="sub"><span class="kicker">{kicker}</span><span class="stat">{stat_html}</span></div></header>"#
    )
}

/// Compose the full `<head>` for a chrome page: meta + OG + no-flash boot + the inlined
/// `@font-face` + `tokens.css` + [`CHROME_CSS`] + the page's own CSS (one critical inline sheet).
#[allow(clippy::too_many_arguments)]
pub fn page_head(
    title: &str,
    description: &str,
    canonical_url: &str,
    feed_url: &str,
    image_tags: &str,
    font_url: &str,
    page_css: &str,
) -> String {
    let style = assets::head_style(&format!("{CHROME_CSS}{page_css}"), font_url);
    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{FAVICON}
{NO_FLASH_SCRIPT}
<link rel="alternate" type="application/atom+xml" title="{title}" href="{canonical_url}{feed_url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical_url}">
<meta property="og:site_name" content="{title}">
<meta name="description" content="{description}">
{image_tags}
{style}
</head>"#
    )
}
