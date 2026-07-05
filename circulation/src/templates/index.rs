//! Index / home — the archive as an issue-numbered running order (`chrome_v12`). The shared frame
//! (top bar, footer, toggle, no-flash) comes from [`super::chrome`]; this module owns the masthead,
//! the search + segment toolbar, the running-order list, the load-more region, and the states
//! (empty archive, subscribe notices, skeleton). Rows are rendered by `archive::row_html` — the same
//! markup the `/archive` load-more fragment serves — so the list has one source of truth.

use super::chrome;
use super::digest::og_image_tags;

/// Index-specific CSS (masthead, toolbar, list rows, states, skeleton). Composed after
/// [`chrome::CHROME_CSS`]; plain raw string so literal `{}` need no escaping. Bias bars use the
/// `--bias-*` tokens (not the mockup's hardcoded hex) so they adapt to dark mode.
const INDEX_CSS: &str = r#"
/* masthead */
.masthead{border-bottom:2px solid var(--ink); padding-bottom:16px;}
.brand{font-family:var(--serif); font-weight:600; font-size:38px; letter-spacing:-.018em; margin:0; line-height:1.02;}
.brand em{color:var(--accent-ink); font-style:normal;}
.sub{display:flex; align-items:baseline; justify-content:space-between; gap:20px; margin-top:12px;}
.kicker{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);}
.stat{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap;}
.stat b{color:var(--ink2); font-weight:600;}

/* notice bar (subscribe success/error) — semantic status axis, redundant glyph+word */
.notice{font-family:var(--sans); font-size:14px; padding:10px 14px; border-radius:var(--r-input);
  background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--muted);
  margin:20px 0 0; display:flex; gap:9px; align-items:baseline;}
.notice .ni{font-family:var(--mono);}
.notice.ok{border-left-color:var(--ok); color:var(--ok-ink);}
.notice.warn{border-left-color:var(--warn); color:var(--warn-ink);}
.notice.bad{border-left-color:var(--accent); color:var(--accent-ink);}

/* toolbar */
.toolbar{display:flex; gap:12px; align-items:center; margin:24px 0 4px; flex-wrap:wrap;}
.search{flex:1; min-width:200px; display:flex;}
.search input{flex:1; font-family:var(--sans); font-size:15px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:10px 13px; min-width:0;}
.search input::placeholder{color:var(--muted);}
.search input:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}
.seg{display:inline-flex; gap:2px; background:var(--wash); border:1px solid var(--line); border-radius:var(--r-input); padding:3px;}
.seg button{font-family:var(--sans); font-size:12px; color:var(--muted); background:transparent; border:none;
  padding:7px 14px; cursor:pointer; border-radius:4px;}
.seg button:hover{color:var(--ink2);}
.seg button[aria-pressed="true"]{background:var(--panel); color:var(--ink); font-weight:600; box-shadow:0 1px 2px rgba(25,25,23,.10);}
.seg button:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

/* date jump */
.datejump{display:inline-flex; align-items:center; gap:8px; font-family:var(--sans); font-size:12px; color:var(--muted); margin-top:10px;}
.datejump input{font-family:var(--sans); font-size:12px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:5px 8px; min-height:24px;}
.datejump input:focus-visible{outline:2px solid var(--accent); outline-offset:1px; border-color:var(--accent);}

/* list header + rows */
.listhead{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; padding:16px 8px 8px;
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  border-bottom:1px solid var(--line-strong);}
.listhead .r{text-align:right;}
ul.index{list-style:none; margin:0; padding:0;}
li.month{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink); padding:22px 0 8px; border-bottom:1px solid var(--ink);}
li.issue{border-bottom:1px solid var(--line);}
li.issue > a{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; align-items:baseline;
  padding:14px 8px; text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
li.issue > a:hover{background:var(--wash);}
li.issue.today > a{background:var(--accent-wash);}
li.issue > a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.idx{display:flex; flex-direction:column; gap:2px; white-space:nowrap;}
.idx .no{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; font-weight:400; order:1;}
.idx .no::before{content:"\00b7\00a0#"; font-weight:400; color:var(--line-strong);}
.idx .date{font-family:var(--mono); font-size:14px; color:var(--ink2); font-variant-numeric:tabular-nums; font-weight:600; order:0;}
.main .sumline{font-family:var(--serif); font-size:17px; font-weight:500; color:var(--ink); line-height:1.4; text-wrap:pretty; display:block; letter-spacing:-.004em;}
li.issue > a:hover .sumline{color:var(--accent-ink);}
.rt{display:flex; flex-direction:column; align-items:flex-end; gap:6px; white-space:nowrap;}
.count{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
.bias{display:inline-flex; height:7px; width:52px; border-radius:2px; overflow:hidden;}
.bias i{display:block; height:100%;}
.bias .l{background:var(--bias-l);} .bias .c{background:var(--bias-c);} .bias .r{background:var(--bias-r);}

/* load-more region + states */
.loadmore{text-align:center; margin:32px 0 0; display:flex; flex-direction:column; align-items:center; gap:10px;}
.loadmore-status{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0; min-height:1em;}
.loadmore.is-error .loadmore-status{color:var(--accent-ink);}
.loadmore-end{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0;}
.empty{font-family:var(--serif); font-size:18px; line-height:1.6; color:var(--muted); max-width:60ch; margin:28px 0 0;}

/* loading skeleton — calm alpha pulse, no shimmer */
.skeleton{border-bottom:1px solid var(--line);}
.skeleton .row{display:grid; grid-template-columns:72px 1fr auto; gap:6px 20px; padding:14px 8px;}
.skeleton .bar{display:block; height:12px; border-radius:3px; background:var(--wash); animation:skel-pulse 1s ease-in-out infinite;}
.skeleton .bar-date{width:44px;} .skeleton .bar-line{width:80%;} .skeleton .bar-bias{width:52px; height:7px;}
@keyframes skel-pulse{0%,100%{opacity:.55} 50%{opacity:1}}
@media (prefers-reduced-motion:reduce){ .skeleton .bar{animation:none; opacity:.7;} }

/* subscribe band — CTA at the end, not a top form-wall */
.subband{display:flex; align-items:center; gap:40px; flex-wrap:wrap; margin-top:40px; padding:24px 28px; background:var(--panel-2); border:1px solid var(--line); border-radius:var(--r-card);}
.subband .copy{flex:0 1 auto; max-width:42ch;}
.subband .copy h2{font-family:var(--serif); font-weight:600; font-size:19px; color:var(--ink); margin:0 0 3px; letter-spacing:-.01em;}
.subband .copy p{font-family:var(--serif); font-size:14px; color:var(--muted); margin:0;}
.subband form{display:flex; gap:8px; flex:1 1 300px;}
.subband input{flex:1; font-family:var(--sans); font-size:14px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-input); padding:10px 13px; min-width:0;}
.subband input::placeholder{color:var(--muted);}
.subband input:focus-visible{outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent);}

@media (max-width:560px){
  .brand{font-size:29px;}
  .sub{flex-direction:column; gap:6px;}
  .toolbar{flex-direction:column; align-items:stretch;}
  .seg{width:100%;} .seg button{flex:1;}
  .listhead{display:none;}
  li.issue > a{grid-template-columns:1fr; gap:6px; padding:16px 4px;}
  .idx{flex-direction:row; align-items:baseline; gap:8px; order:-1;}
  .idx .date::before{content:none;}
  .rt{flex-direction:row; align-items:center; gap:12px; justify-content:flex-start;}
  .subband{flex-direction:column; align-items:stretch; gap:16px;}
  .subband form{flex-direction:column; flex:0 0 auto;} .subband input{flex:0 0 auto; width:100%;} .subband .btn{width:100%;}
}
"#;

/// The index interaction JS: load-more (append `/archive` fragments, read+strip the hidden
/// `.more-sentinel` for the next cursor, focus the first new row, announce "N of TOTAL"), the
/// server-scoped segment control (All/This year/Recent each re-fetch a scope — no client
/// filtering), and the native date-jump. Degrades to real `<a href="/?before=">` navigation with
/// JS off. `View Transitions` wrap the swap when supported (progressive enhancement).
const INDEX_JS: &str = r#"<script>(function(){
  var ul=document.getElementById('index'); if(!ul) return;
  var region=document.getElementById('loadmore');
  var statusNode=region?region.querySelector('.loadmore-status'):null;
  var total=parseInt(ul.getAttribute('data-total')||'0',10);
  var year=String(new Date().getFullYear());
  function issues(){ return ul.querySelectorAll('li.issue').length; }
  function announce(m){ if(statusNode) statusNode.textContent=m; }
  function focusFirstNew(li){ if(!li) return; var a=li.querySelector('a'); if(a) a.focus(); } // the row <a> is already tabbable — don't add tabindex=-1 (would drop it from Tab order)
  function swap(fn){ if(document.startViewTransition){ document.startViewTransition(fn); } else { fn(); } }
  // Parse an <li> fragment (valid only inside a list context), splitting off the end sentinel.
  function parse(html){ var t=document.createElement('ul'); t.innerHTML=html;
    var s=t.querySelector('.more-sentinel'); var next=s?s.getAttribute('data-next-before'):null; if(s) s.remove();
    return {frag:t, next:next}; }
  function setLink(cursor){ var a=document.getElementById('loadMore');
    if(!a){ a=document.createElement('a'); a.className='btn secondary'; a.id='loadMore'; a.setAttribute('rel','next');
      a.textContent='Load older issues'; if(region&&statusNode) region.insertBefore(a, statusNode); }
    a.setAttribute('href','/?before='+cursor); }
  function clearLink(){ var a=document.getElementById('loadMore'); if(a) a.remove(); }
  function endState(){ clearLink(); if(!region) return; var last=ul.querySelector('li.issue:last-child'); var s="That’s the whole archive.";
    if(last){ var d=last.querySelector('.date'); var yr=(last.getAttribute('data-date')||'').slice(0,4);
      if(d&&yr) s="That’s the whole archive, back to "+d.textContent+" "+yr+"."; }
    region.innerHTML='<p class="loadmore-end" role="status">'+s+'</p>'; }

  // Load more (append) — delegated so a re-created button still works.
  function loadMore(a){
    if(a.getAttribute('aria-disabled')==='true') return; // re-entrancy guard: aria-disabled doesn't block <a> clicks
    var m=(a.getAttribute('href')||'').match(/before=([0-9-]+)/); if(!m) return; var cursor=m[1];
    a.setAttribute('aria-busy','true'); a.setAttribute('aria-disabled','true'); if(region) region.classList.remove('is-error');
    announce('Loading older issues…');
    fetch('/archive?before='+encodeURIComponent(cursor)+'&limit=30').then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li.issue');
        // Post-append work runs INSIDE swap: View Transitions defer the DOM mutation, so counting/
        // focusing/announcing outside the callback would read the pre-append (stale) state.
        swap(function(){
          while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
          if(pr.next){ setLink(pr.next); announce('Showing '+issues()+' of '+total+' issues'); } else { endState(); }
          focusFirstNew(first);
        }); })
      .catch(function(){ a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
        if(region) region.classList.add('is-error'); announce("Couldn’t load older issues. Tap to retry."); });
  }
  document.addEventListener('click', function(e){ var a=e.target.closest&&e.target.closest('#loadMore'); if(a){ e.preventDefault(); loadMore(a); } });

  // Segment (replace) — each segment is a server scope; switching resets the list.
  function replaceWith(url, keepLoadmore){
    if(region) region.classList.remove('is-error');
    announce('Loading…');
    fetch(url).then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li.issue');
        swap(function(){
          ul.innerHTML=''; while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          if(keepLoadmore && pr.next){ setLink(pr.next); announce('Showing '+issues()+' of '+total+' issues'); }
          else { clearLink(); announce('Showing '+issues()+(keepLoadmore?' of '+total:'')+' issues'); }
          focusFirstNew(first);
        }); })
      .catch(function(){ if(region) region.classList.add('is-error'); announce("Couldn’t load. Try again."); });
  }
  var segBtns=[].slice.call(document.querySelectorAll('.seg button'));
  segBtns.forEach(function(b){ b.addEventListener('click', function(){
    segBtns.forEach(function(x){ x.setAttribute('aria-pressed', x===b?'true':'false'); });
    var mode=b.getAttribute('data-seg');
    if(mode==='all') replaceWith('/archive?limit=30', true);
    else if(mode==='year') replaceWith('/archive?year='+year, false);
    else replaceWith('/archive?limit=15', false);
  }); });

  var dj=document.getElementById('dateJump');
  if(dj) dj.addEventListener('change', function(){ if(dj.value) location.href='/'+dj.value; });
})();</script>"#;

/// Parameters for the index page — the handler computes the dynamic chunks; the template is the shell.
pub struct IndexParams<'a> {
    pub title: &'a str,
    /// Brand with the accent word wrapped in `<em>` (e.g. `News <em>Digest</em>`).
    pub brand_html: &'a str,
    pub description: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    /// Pre-built shared top bar + footer (config-dependent links live in the handler).
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub kicker: &'a str,
    pub masthead_stat: &'a str,
    /// Subscribe success/error notice, or "".
    pub notice_html: &'a str,
    /// False when the archive is empty — hides toolbar/date-jump/list/load-more, shows `.empty`.
    pub has_issues: bool,
    pub search_url: &'a str,
    /// Which segment is active on load: "all" | "year" | "recent".
    pub segment: &'a str,
    /// Date-jump bounds (first / newest issue date, YYYY-MM-DD).
    pub date_min: &'a str,
    pub date_max: &'a str,
    /// `<li class="month">…</li>` dividers + `<li class="issue">` rows, or the `.empty` state.
    pub list_html: &'a str,
    /// The load-more region (`<div class="loadmore" id="loadmore">…</div>`), or "".
    pub loadmore_html: &'a str,
    /// The subscribe band, or "" when subscriptions are disabled.
    pub subscribe_band: &'a str,
}

fn segment_button(seg: &str, active: &str, label: &str) -> String {
    let pressed = if seg == active { "true" } else { "false" };
    format!(r#"<button type="button" data-seg="{seg}" aria-pressed="{pressed}">{label}</button>"#)
}

/// Render the full index page.
pub fn render_index(p: &IndexParams) -> String {
    let head = chrome::page_head(
        p.title,
        p.description,
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        INDEX_CSS,
    );

    // Toolbar (search + segment + date-jump) and list only exist when there are issues.
    let toolbar = if p.has_issues {
        format!(
            r#"<div class="toolbar">
      <form class="search" role="search" action="{search}" method="get">
        <input type="search" name="q" placeholder="Search past headlines&hellip;" aria-label="Search past headlines">
      </form>
      <div class="seg" role="group" aria-label="Filter by period">
        {all}{year}{recent}
      </div>
    </div>
    <label class="datejump"><span>Jump to</span><input type="date" id="dateJump" min="{min}" max="{max}" aria-label="Jump to a date"></label>
    <div class="listhead"><span>Issue</span><span>In this issue</span><span class="r">Sources</span></div>"#,
            search = p.search_url,
            all = segment_button("all", p.segment, "All"),
            year = segment_button("year", p.segment, "This year"),
            recent = segment_button("recent", p.segment, "Recent"),
            min = p.date_min,
            max = p.date_max,
        )
    } else {
        String::new()
    };

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <header class="masthead">
      <h1 class="brand">{brand}</h1>
      <div class="sub"><span class="kicker">{kicker}</span><span class="stat">{stat}</span></div>
    </header>
    <main id="main">
    {notice}
    {toolbar}
    {list}
    {loadmore}
    {subband}
    </main>
    {footer}
</div></div>
{toggle_js}
{index_js}
</body>
</html>"#,
        head = head,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        brand = p.brand_html,
        kicker = p.kicker,
        stat = p.masthead_stat,
        notice = p.notice_html,
        toolbar = toolbar,
        list = p.list_html,
        loadmore = p.loadmore_html,
        subband = p.subscribe_band,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
        index_js = INDEX_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_params() -> IndexParams<'static> {
        IndexParams {
            title: "News Digest",
            brand_html: "News <em>Digest</em>",
            description: "Daily briefing.",
            canonical_url: "https://example.com",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og-image.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer class=\"site-foot\"></footer>",
            kicker: "All sides, no fluff",
            masthead_stat: "<b>3</b> issues",
            notice_html: "",
            has_issues: true,
            search_url: "/search",
            segment: "all",
            date_min: "2026-01-01",
            date_max: "2026-07-03",
            list_html: "<ul class=\"index\" id=\"index\" data-total=\"3\"></ul>",
            loadmore_html: "",
            subscribe_band: "",
        }
    }

    #[test]
    fn renders_chrome_masthead_and_inlined_tokens() {
        let html = render_index(&base_params());
        assert!(html.contains("<h1 class=\"brand\">News <em>Digest</em></h1>"));
        // tokens + @font-face are inlined via chrome::page_head -> assets::head_style
        assert!(html.contains("@font-face"));
        assert!(html.contains("--accent"));
        assert!(html.contains(r#"src:url("/assets/fonts/x.woff2")"#));
        // one h1, main landmark, skip link
        assert_eq!(html.matches("<h1").count(), 1);
        assert!(html.contains(r#"<main id="main">"#));
        assert!(html.contains(r##"href="#main""##));
    }

    #[test]
    fn all_segment_pressed_by_default_and_search_form_present() {
        let html = render_index(&base_params());
        assert!(html.contains(r#"data-seg="all" aria-pressed="true""#));
        assert!(html.contains(r#"data-seg="year" aria-pressed="false""#));
        assert!(html.contains(r#"data-seg="recent" aria-pressed="false""#));
        assert!(html.contains(r#"action="/search""#));
        assert!(html.contains(r#"min="2026-01-01" max="2026-07-03""#));
    }

    #[test]
    fn this_year_segment_marks_year_pressed() {
        let mut p = base_params();
        p.segment = "year";
        let html = render_index(&p);
        assert!(html.contains(r#"data-seg="year" aria-pressed="true""#));
        assert!(html.contains(r#"data-seg="all" aria-pressed="false""#));
    }

    #[test]
    fn empty_archive_hides_toolbar_and_list_chrome() {
        let mut p = base_params();
        p.has_issues = false;
        p.list_html = r#"<p class="empty">No issues published yet.</p>"#;
        let html = render_index(&p);
        assert!(!html.contains(r#"class="toolbar""#));
        assert!(!html.contains(r#"class="datejump""#));
        assert!(!html.contains(r#"class="listhead""#));
        assert!(html.contains(r#"<p class="empty">No issues published yet.</p>"#));
    }

    #[test]
    fn includes_absolute_og_image_and_twitter_card() {
        let html = render_index(&base_params());
        assert!(
            html.contains(
                r#"<meta property="og:image" content="https://example.com/og-image.png">"#
            )
        );
        assert!(html.contains(r#"<meta name="twitter:card" content="summary_large_image">"#));
    }
}
