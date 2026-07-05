//! Threads — the evolving-story surfaces. The index is a status-grouped list; the detail page is an
//! evolving-story tracker (status + story-so-far standfirst, a "still watching" open-questions ledger,
//! and a dated timeline). Shared frame comes from [`super::chrome`].

use super::chrome;
use super::digest::og_image_tags;
use crate::routes;
use crate::thread::{ThreadDetail, ThreadSummary};
use crate::util::{escape_html, format_day_month_year};

// ───────────────────────── shared status vocabulary ─────────────────────────

/// Thread status -> (marker/label CSS class, display label). `active` is the only "live" state;
/// `dormant` (and anything unrecognised) is the hollow muted state; `closed` is shape-coded closed.
fn status_parts(status: &str) -> (&'static str, &'static str) {
    match status {
        "active" => ("on", "Ongoing"),
        "closed" => ("closed", "Closed"),
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
    pub threads: &'a [ThreadSummary],
}

const THREADS_CSS: &str = r#"
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
.mk.closed{border:1.5px solid var(--line-strong); border-radius:1px; background:transparent;}
.tl .lbl{font-family:var(--serif); font-size:18px; font-weight:600; color:var(--ink); line-height:1.3; letter-spacing:-.006em; display:block; text-wrap:pretty;}
ul.threads li a:hover .lbl{color:var(--accent-ink);}
.tl .last{font-family:var(--serif); font-size:14px; color:var(--muted); margin-top:3px; display:block; line-height:1.45; text-wrap:pretty;}
.meta{text-align:right; white-space:nowrap; display:flex; flex-direction:column; gap:4px; align-items:flex-end;}
.meta .st{font-family:var(--mono); font-size:10px; letter-spacing:.07em; text-transform:uppercase;}
.meta .st.on{color:var(--accent-ink);} .meta .st.dorm, .meta .st.closed{color:var(--muted);}
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

fn thread_section(threads: &[&ThreadSummary], label: &str) -> String {
    if threads.is_empty() {
        return String::new();
    }
    let rows: String = threads.iter().map(|t| thread_row(t)).collect();
    let n = threads.len();
    let noun = if n == 1 { "thread" } else { "threads" };
    format!(
        r#"<section><div class="sec-h"><h2>{label}</h2><span class="ct">{n} {noun}</span></div><ul class="threads">{rows}</ul></section>"#
    )
}

pub fn render_threads_index(p: &ThreadsIndexParams) -> String {
    // Group once by status (input is pre-sorted active-first, so each group keeps its order).
    let mut ongoing: Vec<&ThreadSummary> = Vec::new();
    let mut dormant: Vec<&ThreadSummary> = Vec::new();
    let mut closed: Vec<&ThreadSummary> = Vec::new();
    for t in p.threads {
        match status_parts(&t.status).0 {
            "on" => ongoing.push(t),
            "closed" => closed.push(t),
            _ => dormant.push(t),
        }
    }

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
            "<b>{on}</b> ongoing &middot; <b>{dorm}</b> dormant &middot; <b>{closed}</b> closed",
            on = ongoing.len(),
            dorm = dormant.len(),
            closed = closed.len(),
        ),
    );

    let body = if p.threads.is_empty() {
        r#"<p class="empty">No threads yet — evolving stories appear here once the digest starts tracking them across days.</p>"#.to_string()
    } else {
        format!(
            "{}{}{}",
            thread_section(&ongoing, "Ongoing"),
            thread_section(&dormant, "Dormant"),
            thread_section(&closed, "Closed"),
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
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
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
.dot.closed{border-radius:1px; background:none; border:1.5px solid var(--muted);}
.status{font-family:var(--mono); font-size:11px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink);}
.status.off{color:var(--muted);}
.span{font-family:var(--mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-variant-numeric:tabular-nums;}
.span .divider{color:var(--line-strong); padding:0 2px;}
h1{font-family:var(--serif); font-weight:600; font-size:30px; line-height:1.15; letter-spacing:-.01em; margin:0 0 16px; text-wrap:pretty;}
@media (min-width:760px){ h1{font-size:34px;} }
.sofar{font-family:var(--serif); font-size:18px; line-height:1.55; color:var(--ink2); margin:0; text-wrap:pretty;}
.sofar .lead{font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-right:8px;}
.ledger{border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:var(--r-card); background:var(--panel); padding:20px 24px; margin:0 0 40px;}
.ledger-label{font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--accent-ink); margin:0 0 14px;}
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
            r#"<a class="issue" href="/{d}">&rarr; in the {date} issue</a>"#,
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

    fn idx_params<'a>(threads: &'a [ThreadSummary]) -> ThreadsIndexParams<'a> {
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
            threads,
        }
    }

    #[test]
    fn index_groups_by_status_with_markers() {
        let t = [
            summary(1, "Live story", "active", 12, "latest headline"),
            summary(2, "Quiet story", "dormant", 5, "went quiet"),
        ];
        let html = render_threads_index(&idx_params(&t));
        assert!(
            html.contains("<b>1</b> ongoing &middot; <b>1</b> dormant &middot; <b>0</b> closed")
        );
        assert!(html.contains(r#"<span class="mk on" aria-hidden="true">"#));
        assert!(html.contains(r#"<span class="mk dorm" aria-hidden="true">"#));
        assert!(html.contains(r#"<a href="/thread/1">"#));
        assert!(html.contains("3 Jul 2026 &middot; 12 updates"));
        // closed section omitted (none)
        assert!(!html.contains(r#"<span class="mk closed""#));
    }

    #[test]
    fn index_empty_state() {
        let html = render_threads_index(&idx_params(&[]));
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
        assert!(html.contains(r#"<a class="issue" href="/2026-07-03">"#));
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
