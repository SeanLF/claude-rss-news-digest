//! Threads — the evolving-story surfaces. The index is a status-grouped list; the detail page is an
//! evolving-story tracker (status + story-so-far standfirst, a "still watching" open-questions ledger,
//! and a dated timeline). Shared frame comes from [`super::chrome`].

use super::chrome;
use super::digest::og_image_tags;
use crate::routes;
use crate::thread::{ThreadDetail, ThreadIndexPage, ThreadSummary};
use crate::util::{escape_html, format_day_month_year};

// ───────────────────────── shared status vocabulary ─────────────────────────

/// Thread status -> (marker/label CSS class, display label). `active` is the only "live" state;
/// `dormant` -- and anything unrecognised -- is the hollow muted state. There is deliberately no
/// `closed` arm: nothing in the pipeline ever writes that status (`decay_threads` is the only
/// writer of `threads.status` and only ever writes 'dormant'), so the branch and its CSS were
/// rendering a state that cannot occur.
fn status_parts(status: &str) -> (&'static str, &'static str) {
    match status {
        "active" => ("on", "Ongoing"),
        _ => ("dorm", "Dormant"),
    }
}

/// The date portion of a stored timestamp (`YYYY-MM-DD...`), formatted `3 Jul 2026`.
fn fmt_ts_date(ts: &str) -> String {
    format_day_month_year(ts.get(0..10).unwrap_or(ts))
}

// ───────────────────────────── threads index ────────────────────────────────

pub struct ThreadsIndexParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub page: &'a ThreadIndexPage,
    /// True on a `?before=` page. Such a page is reached without JS or from a shared link, so the
    /// only route back to the newest threads would otherwise be the footer, below thirty rows.
    /// The archive's `?before=` branch carries the same affordance.
    pub deep: bool,
}

const THREADS_CSS: &str = r#"
.loadmore{text-align:center; margin:32px 0 0; display:flex; flex-direction:column; align-items:center; gap:10px;}
.loadmore-status{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0; min-height:1em;}
.loadmore.is-error .loadmore-status{color:var(--accent-ink);}
.loadmore-end{font-family:var(--mono); font-size:12px; color:var(--muted); margin:0;}
section{margin-top:40px;}
.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}
.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}
.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}
ul.threads{list-style:none; margin:0; padding:0;}
ul.threads li{border-top:1px solid var(--line);}
ul.threads li:first-child{border-top:none;}
ul.threads li a{display:grid; grid-template-columns:auto 1fr auto; gap:14px; align-items:baseline; padding:15px 8px;
  text-decoration:none; color:inherit; border-radius:6px; transition:background .08s;}
ul.threads li a:hover{background:var(--wash);}
ul.threads li a:focus-visible{outline:2px solid var(--accent); outline-offset:-2px;}
.mk{width:9px; height:9px; margin-top:7px;}
.mk.on{background:var(--accent); border-radius:999px;}
.mk.dorm{border:1.5px solid var(--muted); border-radius:999px; background:transparent;}
.tl .lbl{font-family:var(--serif); font-size:18px; font-weight:600; color:var(--ink); line-height:1.3; letter-spacing:-.006em; display:block; text-wrap:pretty;}
ul.threads li a:hover .lbl{color:var(--accent-ink);}
.tl .last{font-family:var(--serif); font-size:14px; color:var(--muted); margin-top:3px; display:block; line-height:1.45; text-wrap:pretty;}
.meta{text-align:right; white-space:nowrap; display:flex; flex-direction:column; gap:4px; align-items:flex-end;}
.meta .st{font-family:var(--mono); font-size:10px; letter-spacing:.07em; text-transform:uppercase;}
.meta .st.on{color:var(--accent-ink);} .meta .st.dorm{color:var(--muted);}
.meta .upd{font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
.empty{font-family:var(--serif); font-size:18px; color:var(--muted); margin-top:28px;}
@media (max-width:560px){
  ul.threads li a{grid-template-columns:auto 1fr; gap:12px;}
  .meta{grid-column:2; flex-direction:row; align-items:baseline; gap:10px; margin-top:2px;}
}
"#;

fn thread_row(t: &ThreadSummary) -> String {
    let (cls, label) = status_parts(&t.status);
    let last = if t.summary.is_empty() {
        String::new()
    } else {
        format!(r#"<span class="last">{}</span>"#, escape_html(&t.summary))
    };
    format!(
        r#"<li><a href="{thread}/{id}"><span class="mk {cls}" aria-hidden="true"></span><span class="tl"><span class="lbl">{lbl}</span>{last}</span><span class="meta"><span class="st {cls}">{label}</span><span class="upd">{date} &middot; {n} update{s}</span></span></a></li>"#,
        thread = routes::THREAD,
        id = t.id,
        lbl = escape_html(&t.label),
        date = fmt_ts_date(&t.updated_at),
        n = t.update_count,
        s = if t.update_count == 1 { "" } else { "s" },
    )
}

/// One section. `total` is the FULL count for the heading, which for a paged section is larger
/// than the rows rendered -- saying "30 threads" above a paged list of 607 would be a lie.
fn thread_section(threads: &[ThreadSummary], label: &str, total: i64, id: &str) -> String {
    if threads.is_empty() {
        return String::new();
    }
    let rows: String = threads.iter().map(thread_row).collect();
    let noun = if total == 1 { "thread" } else { "threads" };
    format!(
        r#"<section><div class="sec-h"><h2>{label}</h2><span class="ct">{total} {noun}</span></div><ul class="threads" id="{id}" data-total="{total}">{rows}</ul></section>"#
    )
}

/// Hidden `<li>` carrying the next cursor, stripped by the load-more JS before the rows are
/// appended. Same technique as the archive fragment: the cursor rides in the markup rather than in
/// a parallel JSON envelope, so the fragment stays a list the browser can render unaided.
fn more_sentinel(next: &Option<(String, i64)>) -> String {
    match next {
        Some((ts, id)) => format!(
            r#"<li class="more-sentinel" hidden data-next-before="{ts}" data-next-id="{id}"></li>"#,
            ts = escape_html(ts),
        ),
        None => String::new(),
    }
}

/// The load-more control. A real `<a href>` so the list pages with JS off -- the href is the same
/// cursor the fragment endpoint takes, pointed at the full page.
fn loadmore_region(next: &Option<(String, i64)>) -> String {
    match next {
        Some((ts, id)) => format!(
            r#"<div class="loadmore" id="loadmore"><a class="btn secondary" id="loadMore" rel="next" href="{threads}?before={ts}&amp;before_id={id}">Load older threads</a><p class="loadmore-status" role="status" aria-live="polite"></p></div>"#,
            threads = routes::THREADS,
            ts = escape_html(&urlencode(ts)),
        ),
        None => String::new(),
    }
}

/// Percent-encode a value for a query string. Callers MUST still `escape_html` the result before
/// it goes in an attribute: percent-encoding and HTML escaping answer different sinks, and this
/// one deliberately passes everything it does not recognise through untouched -- a tab is a valid
/// attribute separator, so an unescaped one ends the href and starts a new attribute.
fn urlencode(s: &str) -> String {
    // Allowlist, not a blocklist of the characters today's values happen to contain. The previous
    // version mapped space, colon and plus and passed everything else through -- including a tab,
    // which is a valid HTML attribute separator.
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// A `?before=` page is reached without JS or from a shared link, so its only route back to the
/// newest threads would otherwise be the footer, below thirty rows. The archive's `?before=`
/// branch carries the same affordance.
fn back_to_newest(deep: bool) -> String {
    if !deep {
        return String::new();
    }
    format!(
        r#"<p style="text-align:center;margin-top:16px"><a href="{}">&uarr; Back to the newest threads</a></p>"#,
        routes::THREADS
    )
}

/// The older-threads rows plus the cursor sentinel, for `GET /threads/more`.
pub fn render_threads_fragment(page: &ThreadIndexPage) -> String {
    let rows: String = page.older.iter().map(thread_row).collect();
    format!("{rows}{}", more_sentinel(&page.next_before))
}

/// Load-more for the "Earlier" list: append `/threads/more` fragments, read+strip the hidden
/// `.more-sentinel` for the next cursor, focus the first new row, announce "N of TOTAL". Degrades
/// to a real `<a href="/threads?before=…">` page load with JS off, so the whole list stays
/// reachable without scripting and to a crawler. Mirrors the index's load-more; kept separate
/// because this page has no segment control or date jump to share with it.
const THREADS_JS: &str = r#"<script>(function(){
  var ul=document.getElementById('older-threads'); if(!ul) return;
  var region=document.getElementById('loadmore');
  var statusNode=region?region.querySelector('.loadmore-status'):null;
  var total=parseInt(ul.getAttribute('data-total')||'0',10);
  function rows(){ return ul.querySelectorAll('li').length; }
  function announce(m){ if(statusNode) statusNode.textContent=m; }
  function swap(fn){ if(document.startViewTransition){ document.startViewTransition(fn); } else { fn(); } }
  function parse(html){ var t=document.createElement('ul'); t.innerHTML=html;
    var s=t.querySelector('.more-sentinel');
    var next=s?{ts:s.getAttribute('data-next-before'), id:s.getAttribute('data-next-id')}:null;
    if(s) s.remove();
    return {frag:t, next:next}; }
  function href(n){ return '/threads?before='+encodeURIComponent(n.ts)+'&before_id='+encodeURIComponent(n.id); }
  function setLink(n){ var a=document.getElementById('loadMore'); if(a) a.setAttribute('href', href(n)); }
  function endState(){ var a=document.getElementById('loadMore'); if(a) a.remove();
    if(region) region.innerHTML='<p class="loadmore-end" role="status">That’s every thread.</p>'; }
  function loadMore(a){
    if(a.getAttribute('aria-disabled')==='true') return; // aria-disabled doesn't block <a> clicks
    var h=a.getAttribute('href')||''; var qs=h.slice(h.indexOf('?')+1);
    a.setAttribute('aria-busy','true'); a.setAttribute('aria-disabled','true');
    if(region) region.classList.remove('is-error');
    announce('Loading older threads…');
    fetch('/threads/more?'+qs).then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){ var pr=parse(html); var first=pr.frag.querySelector('li');
        // Inside swap(): View Transitions defer the mutation, so counting/focusing outside it
        // would read the pre-append state.
        swap(function(){
          while(pr.frag.firstChild) ul.appendChild(pr.frag.firstChild);
          a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
          if(pr.next){ setLink(pr.next); announce('Showing '+rows()+' of '+total+' earlier threads'); }
          else { endState(); }
          if(first){ var link=first.querySelector('a'); if(link) link.focus(); }
        }); })
      .catch(function(){ a.removeAttribute('aria-busy'); a.removeAttribute('aria-disabled');
        if(region) region.classList.add('is-error'); announce('Couldn’t load older threads. Tap to retry.'); });
  }
  document.addEventListener('click', function(e){ var a=e.target.closest&&e.target.closest('#loadMore'); if(a){ e.preventDefault(); loadMore(a); } });
})();</script>"#;

pub fn render_threads_index(p: &ThreadsIndexParams) -> String {
    let page = p.page;
    let is_empty = page.ongoing.is_empty() && page.older.is_empty();

    let head = chrome::page_head(
        p.title,
        "Ongoing stories the digest is tracking across days.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        THREADS_CSS,
    );
    let masthead = chrome::sub_masthead(
        p.home_url,
        p.brand_html,
        "Threads",
        "Ongoing stories, tracked across days",
        &format!(
            "<b>{on}</b> ongoing &middot; <b>{older}</b> earlier",
            on = page.ongoing.len(),
            older = page.older_total,
        ),
    );

    let body = if is_empty {
        r#"<p class="empty">No threads yet — evolving stories appear here once the digest starts tracking them across days.</p>"#.to_string()
    } else {
        format!(
            "{ongoing}{older}{more}{back}",
            ongoing = thread_section(
                &page.ongoing,
                "Ongoing",
                page.ongoing.len() as i64,
                "ongoing-threads"
            ),
            older = thread_section(&page.older, "Earlier", page.older_total, "older-threads"),
            more = loadmore_region(&page.next_before),
            back = back_to_newest(p.deep),
        )
    };

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    {masthead}
    <main id="main">
    {body}
    </main>
    {footer}
</div></div>
{toggle_js}
{threads_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        threads_js = THREADS_JS,
        topbar = p.topbar_html,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

// ─────────────────────────── thread detail ──────────────────────────────────

pub struct ThreadParams<'a> {
    pub brand_html: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub detail: &'a ThreadDetail,
}

const THREAD_CSS: &str = r#"
.mast{border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:24px;}
.brandline{font-family:var(--serif); font-size:15px; color:var(--muted); margin:0 0 14px;}
.brandline em{color:var(--accent-ink); font-style:normal; font-weight:600;}
.statusrow{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:0 0 10px;}
.dot{width:9px; height:9px; border-radius:50%; flex:none;}
.dot.on{background:var(--accent);}
.dot.dorm{background:none; border:1.5px solid var(--muted);}
.status{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink);}
.status.off{color:var(--muted);}
.span{font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-variant-numeric:tabular-nums;}
.span .divider{color:var(--line-strong); padding:0 2px;}
h1{font-family:var(--serif); font-weight:600; font-size:30px; line-height:1.15; letter-spacing:-.01em; margin:0 0 16px; text-wrap:pretty;}
@media (min-width:760px){ h1{font-size:34px;} }
.sofar{font-family:var(--serif); font-size:18px; line-height:1.55; color:var(--ink2); margin:0; text-wrap:pretty;}
.sofar .lead{font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-right:8px;}
/* An editorial block, not an alert box: a full-width hairline rule + mono label, matching the
   page's ruled-section language. The label keeps the lone accent as a live-tracking signal. */
.ledger{border-top:1px solid var(--line); padding:24px 0 0; margin:0 0 44px;}
.ledger-label{display:flex; align-items:center; gap:8px; font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink); margin:0 0 16px;}
.ledger-label::before{content:""; width:6px; height:6px; border-radius:50%; background:var(--accent); flex:none;}
.qlist{list-style:none; margin:0; padding:0;}
.qlist li{position:relative; padding:0 0 0 20px; margin:0 0 12px; color:var(--ink2); font-size:16px; line-height:1.5; text-wrap:pretty;}
.qlist li:last-child{margin-bottom:0;}
.qlist li::before{content:""; position:absolute; left:2px; top:.62em; width:6px; height:6px; border-radius:50%; border:1.5px solid var(--muted);}
.tl-label{font-family:var(--sans); font-size:11px; font-weight:700; letter-spacing:.18em; text-transform:uppercase; color:var(--ink); margin:0 0 24px; padding-bottom:8px; border-bottom:1px solid var(--line);}
.timeline{list-style:none; margin:0; padding:0; position:relative;}
.update{position:relative; padding:0 0 34px 30px;}
.update::before{content:""; position:absolute; left:0; top:5px; width:9px; height:9px; border-radius:50%; background:var(--muted); z-index:1;}
.update.quiet::before{background:none; border:1.5px solid var(--line-strong);}
.update:not(:last-child)::after{content:""; position:absolute; left:4px; top:12px; bottom:-2px; width:1px; background:var(--line);}
.when{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0 0 6px; font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted);}
.when .date{color:var(--ink2); font-weight:600;}
.when .issue{font-family:var(--mono); font-size:11px; color:var(--accent-ink); text-decoration:none;}
.when .issue:hover{text-decoration:underline;}
.uhead{font-family:var(--serif); font-weight:600; font-size:20px; line-height:1.28; letter-spacing:-.01em; margin:0 0 12px; text-wrap:pretty;}
.update.quiet .uhead{font-size:16px; color:var(--muted); font-weight:400; font-style:italic;}
.facts{list-style:none; margin:0; padding:0;}
.facts>li{position:relative; padding:0 0 0 18px; margin:0 0 12px; color:var(--ink2); line-height:1.55; text-wrap:pretty;}
.facts>li::before{content:""; position:absolute; left:0; top:.68em; width:5px; height:5px; background:var(--line-strong);}
.backlink{font-family:var(--sans); font-size:13px; margin-top:8px;}
.backlink a{color:var(--muted); text-decoration:none;} .backlink a:hover{color:var(--accent-ink);}
"#;

fn timeline_entry(e: &crate::thread::ThreadEntry) -> String {
    let issue = match &e.digest_date {
        Some(d) => format!(
            r#"<a class="issue" href="{issues}/{d}">&rarr; in the {date} issue</a>"#,
            issues = routes::ISSUES,
            date = format_day_month_year(d)
        ),
        None => String::new(),
    };
    let when = format!(
        r#"<div class="when"><span class="date">{date}</span>{issue}</div>"#,
        date = format_day_month_year(&e.day),
    );
    // A stored installment with no renderable facts is a "carried forward" quiet day.
    if e.facts.is_empty() {
        let head = if e.headline.is_empty() {
            "No new developments — the thread carried forward without a fresh installment."
                .to_string()
        } else {
            escape_html(&e.headline)
        };
        return format!(r#"<li class="update quiet">{when}<h3 class="uhead">{head}</h3></li>"#);
    }
    let facts: String = e
        .facts
        .iter()
        .map(|f| format!("<li>{}</li>", escape_html(f)))
        .collect();
    format!(
        r#"<li class="update">{when}<h3 class="uhead">{head}</h3><ul class="facts">{facts}</ul></li>"#,
        head = escape_html(&e.headline),
    )
}

pub fn render_thread(p: &ThreadParams) -> String {
    let d = p.detail;
    let (dot_cls, status_label) = status_parts(&d.status);
    let status_off = if dot_cls == "on" { "" } else { " off" };

    // Story-so-far (MVP): the latest installment's delta prose.
    let story_so_far = d
        .entries
        .first()
        .map(|e| e.facts.join(" "))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            "This thread is being tracked; the first installment will appear here.".to_string()
        });

    let since = d
        .entries
        .last()
        .map(|e| format_day_month_year(&e.day))
        .unwrap_or_default();
    let updates = d.entries.len();

    let head = chrome::page_head(
        &escape_html(&d.label),
        &escape_html(&story_so_far),
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        THREAD_CSS,
    );

    // "Still watching" ledger (only when there are open questions).
    let ledger = if d.open_questions.is_empty() {
        String::new()
    } else {
        let qs: String = d
            .open_questions
            .iter()
            .map(|q| format!("<li>{}</li>", escape_html(q)))
            .collect();
        format!(
            r#"<section class="ledger" aria-labelledby="watch"><h2 id="watch" class="ledger-label">Still watching</h2><ul class="qlist">{qs}</ul></section>"#
        )
    };

    let timeline: String = d.entries.iter().map(timeline_entry).collect();

    // "N updates · since {date}" — drop the "since" clause when there are no installments yet.
    let plural = if updates == 1 { "" } else { "s" };
    let meta_span = if since.is_empty() {
        format!("{updates} update{plural}")
    } else {
        format!(
            r#"{updates} update{plural}<span class="divider" aria-hidden="true">·</span>since {since}"#
        )
    };

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <header class="mast">
      <p class="brandline">{brand}</p>
      <div class="statusrow">
        <span class="dot {dot_cls}" aria-hidden="true"></span>
        <span class="status{status_off}">{status_label}</span>
        <span class="span">{meta_span}</span>
      </div>
      <h1>{label}</h1>
      <p class="sofar"><span class="lead">The story so far</span>{story}</p>
    </header>
    <main id="main">
      {ledger}
      <section aria-labelledby="tl">
        <h2 id="tl" class="tl-label">How it developed</h2>
        <ol class="timeline">{timeline}</ol>
      </section>
      <p class="backlink"><a href="{threads}">&larr; All threads</a></p>
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        brand = p.brand_html,
        label = escape_html(&d.label),
        story = escape_html(&story_so_far),
        threads = routes::THREADS,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::thread::ThreadEntry;

    fn summary(id: i64, label: &str, status: &str, n: i64, sum: &str) -> ThreadSummary {
        ThreadSummary {
            id,
            label: label.into(),
            status: status.into(),
            updated_at: "2026-07-03 08:00:00".into(),
            update_count: n,
            summary: sum.into(),
        }
    }

    /// Split a flat fixture list the way the query does: active is never paged, the rest is.
    fn idx_page(threads: &[ThreadSummary]) -> ThreadIndexPage {
        let (ongoing, older): (Vec<_>, Vec<_>) =
            threads.iter().cloned().partition(|t| t.status == "active");
        let older_total = older.len() as i64;
        ThreadIndexPage {
            ongoing,
            older,
            older_total,
            next_before: None,
        }
    }

    fn idx_params<'a>(page: &'a ThreadIndexPage) -> ThreadsIndexParams<'a> {
        ThreadsIndexParams {
            title: "Threads",
            brand_html: "News <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://example.com/threads",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            page,
            deep: false,
        }
    }

    #[test]
    fn a_cursor_cannot_break_out_of_the_load_more_href() {
        // percent-encoding is not HTML escaping. `urlencode` maps only space, colon and plus, so a
        // TAB -- a valid attribute separator -- used to close the href and open a new attribute,
        // landing a live event handler on the anchor. The sink decides the escaping, not the
        // value's provenance.
        let page = ThreadIndexPage {
            ongoing: vec![],
            older: vec![summary(1, "x", "dormant", 1, "")],
            older_total: 40,
            next_before: Some((
                "2026-06-27 08:00:00\"\tonmouseover=alert(1) x=\"".to_string(),
                7,
            )),
        };
        let html = render_threads_index(&idx_params(&page));
        // The breakout signature is a RAW quote closing the href, followed by the handler. Escaped
        // correctly, the payload stays inside the attribute value, so its text is still present in
        // the source -- asserting on the text alone would pass even when the tag is broken.
        assert!(
            !html.contains("\"\tonmouseover"),
            "cursor escaped its attribute: {}",
            html.split("loadMore").nth(1).unwrap_or("")
        );
        assert!(
            !html.contains("2026-06-27 08:00:00"),
            "the cursor must be percent-encoded, not raw"
        );
        assert!(
            html.contains(r#"id="loadMore""#),
            "the button should still render"
        );
    }

    #[test]
    fn index_groups_by_status_with_markers() {
        let t = [
            summary(1, "Live story", "active", 12, "latest headline"),
            summary(2, "Quiet story", "dormant", 5, "went quiet"),
        ];
        let html = render_threads_index(&idx_params(&idx_page(&t)));
        assert!(html.contains("<b>1</b> ongoing &middot; <b>1</b> earlier"));
        assert!(html.contains(r#"<span class="mk on" aria-hidden="true">"#));
        assert!(html.contains(r#"<span class="mk dorm" aria-hidden="true">"#));
        assert!(html.contains(r#"<a href="/thread/1">"#));
        assert!(html.contains("3 Jul 2026 &middot; 12 updates"));
        // No "closed" vocabulary anywhere: nothing writes that status, so nothing renders it.
        assert!(!html.contains("closed"));
    }

    #[test]
    fn index_empty_state() {
        let html = render_threads_index(&idx_params(&idx_page(&[])));
        assert!(html.contains(r#"class="empty""#));
        assert!(!html.contains(r#"class="threads""#));
    }

    fn entry(day: &str, digest: Option<&str>, head: &str, facts: &[&str]) -> ThreadEntry {
        ThreadEntry {
            day: day.into(),
            digest_date: digest.map(String::from),
            headline: head.into(),
            facts: facts.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn detail_params<'a>(d: &'a ThreadDetail) -> ThreadParams<'a> {
        ThreadParams {
            brand_html: "News <em>Digest</em>",
            canonical_url: "https://example.com/thread/1",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            detail: d,
        }
    }

    #[test]
    fn detail_renders_status_ledger_and_timeline() {
        let d = ThreadDetail {
            label: "Ceasefire talks".into(),
            status: "active".into(),
            entries: vec![
                entry(
                    "2026-07-03",
                    Some("2026-07-03"),
                    "Talks resume",
                    &["A deal was signed.", "Hormuz reopened."],
                ),
                entry("2026-06-23", None, "Thread opened", &["Framework floated."]),
            ],
            open_questions: vec!["When is the next round?".into(), "Who leads it?".into()],
        };
        let html = render_thread(&detail_params(&d));
        // status marker + label
        assert!(html.contains(r#"<span class="dot on" aria-hidden="true">"#));
        assert!(html.contains(r#"<span class="status">Ongoing</span>"#));
        assert!(html.contains("2 updates"));
        assert!(html.contains("since 23 Jun 2026"));
        // story-so-far = latest facts joined
        assert!(html.contains("A deal was signed. Hormuz reopened."));
        // ledger
        assert!(html.contains(r#"<h2 id="watch" class="ledger-label">Still watching</h2>"#));
        assert!(html.contains("<li>When is the next round?</li>"));
        // timeline: headline + facts + issue link
        assert!(html.contains(r#"<h3 class="uhead">Talks resume</h3>"#));
        assert!(html.contains("<li>Hormuz reopened.</li>"));
        assert!(html.contains(r#"<a class="issue" href="/issues/2026-07-03">"#));
        assert_eq!(html.matches("<h1").count(), 1);
    }

    #[test]
    fn detail_quiet_day_and_no_ledger() {
        let d = ThreadDetail {
            label: "Quiet".into(),
            status: "dormant".into(),
            entries: vec![entry("2026-06-28", None, "", &[])],
            open_questions: vec![],
        };
        let html = render_thread(&detail_params(&d));
        assert!(html.contains(r#"<li class="update quiet">"#));
        assert!(html.contains("No new developments"));
        assert!(html.contains(r#"<span class="status off">Dormant</span>"#));
        assert!(!html.contains("Still watching")); // no open questions -> no ledger
    }

    #[test]
    fn detail_escapes_label_and_facts() {
        let d = ThreadDetail {
            label: "A & <b>".into(),
            status: "active".into(),
            entries: vec![entry("2026-07-03", None, "H & <i>", &["fact <x>"])],
            open_questions: vec!["q & <y>".into()],
        };
        let html = render_thread(&detail_params(&d));
        assert!(html.contains("A &amp; &lt;b&gt;"));
        assert!(html.contains("fact &lt;x&gt;"));
        assert!(html.contains("q &amp; &lt;y&gt;"));
    }
}
