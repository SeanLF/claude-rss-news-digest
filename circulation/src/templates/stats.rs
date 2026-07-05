//! Stats page — editorial health: balance (bias mix + JSD), source-concentration (HHI) + coverage,
//! source-health RAG, cost, dedup, and honest "Planned" roadmap tiles. Shared frame from
//! [`super::chrome`]; the metrics are computed in `crate::stats::compute_metrics`.

use super::chrome;
use super::digest::og_image_tags;
use crate::stats::{StatsData, StatsMetrics, source_names};
use crate::util::escape_html;

pub struct StatsParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    pub days: u32,
    pub data: &'a StatsData,
    pub metrics: &'a StatsMetrics,
}

const STATS_CSS: &str = r#"
.toolbar{margin:24px 0 0;}
.seg{display:inline-flex; gap:2px; background:var(--wash); border:1px solid var(--line); border-radius:var(--r-input); padding:3px;}
.seg a{font-family:var(--sans); font-size:12px; color:var(--muted); text-decoration:none; padding:7px 15px; border-radius:4px;}
.seg a:hover{color:var(--ink2);}
.seg a[aria-current="true"]{background:var(--panel); color:var(--ink); font-weight:600; box-shadow:0 1px 2px rgba(25,25,23,.10);}
.seg a:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
section{margin-top:44px;}
.sec-h{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); padding-bottom:8px;}
.sec-h h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--ink); margin:0;}
.sec-h .ct{font-family:var(--mono); font-size:11px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums;}
.planned-badge{font-family:var(--mono); font-size:9px; letter-spacing:.1em; text-transform:uppercase; color:var(--warn-ink);
  border:1px solid var(--line-strong); border-radius:3px; padding:2px 6px;}
.note{font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.6; margin:14px 0 0;}
.note code{font-family:var(--mono); font-size:12px; color:var(--ink2);}
.stats{display:flex; flex-wrap:wrap; gap:14px 40px; margin-top:16px;}
.st .v{font-family:var(--serif); font-weight:600; font-size:27px; color:var(--ink); line-height:1; font-variant-numeric:tabular-nums; letter-spacing:-.01em; display:block;}
.st .v.ok{color:var(--ok-ink);} .st .v.warn{color:var(--warn-ink);} .st .v.bad{color:var(--accent-ink);}
.st .l{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin-top:6px; display:block;}
.st .s{font-family:var(--sans); font-size:12px; color:var(--ink2); margin-top:2px; display:block;}
/* balance spectrum */
.bal{margin-top:20px;}
.balrow{display:grid; grid-template-columns:76px 1fr; gap:14px; align-items:center; margin-bottom:8px;}
.balrow .rl{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); text-align:right;}
.sbar{display:grid; grid-template-columns:repeat(7,1fr); height:22px; border-radius:5px; overflow:hidden; border:1px solid var(--line);}
.sbar .z{display:block;}
.sbar .e-l{background:color-mix(in srgb, var(--bias-l) 12%, var(--paper));}
.sbar .e-r{background:color-mix(in srgb, var(--bias-r) 12%, var(--paper));}
.sbar .on-l{background:var(--bias-l);} .sbar .on-c{background:var(--bias-c);} .sbar .on-r{background:var(--bias-r);}
.balnums{display:grid; grid-template-columns:76px 1fr; margin:2px 0;}
.balnums .sp{grid-column:2; display:grid; grid-template-columns:repeat(7,1fr);}
.balnums .sp span{text-align:center; font-family:var(--mono); font-size:11px; color:var(--ink2); font-variant-numeric:tabular-nums;}
.balnums.cat .sp span{color:var(--muted);}
.ballabels{display:grid; grid-template-columns:76px 1fr;}
.ballabels .sp{grid-column:2; display:grid; grid-template-columns:repeat(7,1fr); margin-top:6px;}
.ballabels .sp span{text-align:center; font-family:var(--mono); font-size:8.5px; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); padding:0 1px; line-height:1.3;}
.ballabels .sp span.on{color:var(--ink2);}
/* concentration share bars */
.shares{margin-top:16px; display:flex; flex-direction:column; gap:8px;}
.share{display:grid; grid-template-columns:130px 1fr 42px; gap:12px; align-items:center;}
.share .sn{font-family:var(--serif); font-size:14px; color:var(--ink2);}
.share .track{height:8px; background:var(--wash); border-radius:999px; overflow:hidden;}
.share .fill{height:100%; background:var(--bias-c); border-radius:999px;}
.share .pc{font-family:var(--mono); font-size:12px; color:var(--muted); text-align:right; font-variant-numeric:tabular-nums;}
.drill{font-family:var(--sans); font-size:13px; color:var(--muted); margin-top:14px;}
.drill code{font-family:var(--mono); font-size:12px; color:var(--ink2);}
/* tables */
.tbl-wrap{overflow-x:auto; margin-top:8px;}
table{width:100%; border-collapse:collapse; min-width:460px;}
thead th{font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  font-weight:400; text-align:left; padding:12px 10px 8px; border-bottom:1px solid var(--line-strong); white-space:nowrap;}
thead th.n{text-align:right;}
tbody tr:hover{background:var(--wash);}
tbody td{padding:10px; border-bottom:1px solid var(--line); vertical-align:baseline;}
td.src{font-family:var(--serif); font-size:15px; font-weight:600; color:var(--ink);}
td.n{font-family:var(--mono); font-size:13px; color:var(--ink2); text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
td.time{font-family:var(--mono); font-size:12.5px; color:var(--ink2); white-space:nowrap; font-variant-numeric:tabular-nums;}
.rate{display:inline-flex; align-items:center; gap:6px; font-family:var(--mono); font-size:13px; font-variant-numeric:tabular-nums; justify-content:flex-end;}
.rate .ic{font-family:var(--sans); font-size:11px;}
.rate.ok{color:var(--ok-ink);} .rate.ok .ic{color:var(--ok);}
.rate.warn{color:var(--warn-ink);} .rate.warn .ic{color:var(--warn);}
.rate.bad{color:var(--accent-ink);} .rate.bad .ic{color:var(--accent);}
td.rate-cell{text-align:right;}
/* planned roadmap */
.planned{border:1px dashed var(--line-strong); border-radius:var(--r-card); padding:18px 20px; margin-top:14px; background:color-mix(in srgb, var(--warn) 4%, transparent);}
.planned .stats .v{color:var(--muted);}
@media (max-width:560px){
  .balrow{grid-template-columns:1fr;} .balrow .rl{text-align:left;}
  .ballabels{grid-template-columns:1fr;} .ballabels .sp{grid-column:1;}
  .share{grid-template-columns:100px 1fr 40px;}
}
"#;

fn period_label(days: u32) -> String {
    format!("last {days} days")
}

/// success rate -> (rate class, glyph). Semantic axis only.
fn rag(rate: f64) -> (&'static str, &'static str) {
    if rate >= 95.0 {
        ("ok", "✓")
    } else if rate >= 80.0 {
        ("warn", "▲")
    } else {
        ("bad", "✕")
    }
}

/// A `.st` stat tile.
fn tile(value_html: &str, value_cls: &str, label: &str, sub: &str) -> String {
    let sub = if sub.is_empty() {
        String::new()
    } else {
        format!(r#"<span class="s">{sub}</span>"#)
    };
    format!(
        r#"<span class="st"><span class="v {value_cls}">{value_html}</span><span class="l">{label}</span>{sub}</span>"#
    )
}

/// A 7-cell spectrum bar row (row-label + saturated l/c/r cells, faded extremes).
fn spectrum_row(label: &str, aria: &str) -> String {
    format!(
        r#"<div class="balrow"><span class="rl">{label}</span><div class="sbar" role="img" aria-label="{aria}"><span class="z e-l"></span><span class="z e-l"></span><span class="z on-l"></span><span class="z on-c"></span><span class="z on-r"></span><span class="z e-r"></span><span class="z e-r"></span></div></div>"#
    )
}

/// The 7-cell number row above/below a spectrum bar (only l/c/r columns carry text).
fn spectrum_nums(pct: [i64; 3], cat: bool) -> String {
    let cls = if cat { "balnums cat" } else { "balnums" };
    format!(
        r#"<div class="{cls}"><div class="sp"><span></span><span></span><span>{}%</span><span>{}%</span><span>{}%</span><span></span><span></span></div></div>"#,
        pct[0], pct[1], pct[2]
    )
}

fn balance_section(m: &StatsMetrics) -> String {
    let (bal_verdict, bal_cls) = if m.jsd < 0.05 {
        ("Tracks catalog", "ok")
    } else if m.jsd < 0.15 {
        ("Slight lean", "warn")
    } else {
        ("Skews the shelf", "bad")
    };
    let fact_cls = if m.factuality_high_pct >= 90 {
        "ok"
    } else {
        "warn"
    };
    let tiles = format!(
        "{}{}{}",
        tile(
            bal_verdict,
            bal_cls,
            "Selection vs shelf",
            &format!("JSD {:.2} — curation isn't the skew", m.jsd)
        ),
        tile(
            &format!("{} of 7", m.buckets_sourced),
            "warn",
            "Spectrum buckets sourced",
            "bounded by the factuality floor"
        ),
        tile(
            &format!("{}%", m.factuality_high_pct),
            fact_cls,
            "Shipped ≥ high factuality",
            "the bar the extremes fail"
        ),
    );
    let shipped_aria = format!(
        "Shipped mix: {}% lean-left, {}% centre, {}% lean-right",
        m.shipped_pct[0], m.shipped_pct[1], m.shipped_pct[2]
    );
    let catalog_aria = format!(
        "Catalog mix: {}% lean-left, {}% centre, {}% lean-right",
        m.catalog_pct[0], m.catalog_pct[1], m.catalog_pct[2]
    );
    format!(
        r##"<section>
  <div class="sec-h"><h2>Balance</h2><span class="ct">shipped vs catalog</span></div>
  <div class="stats">{tiles}</div>
  <div class="bal">
    {ship_nums}
    {ship_row}
    {cat_row}
    {cat_nums}
    <div class="ballabels"><div class="sp"><span>Far left</span><span>Left</span><span class="on">Lean left</span><span class="on">Centre</span><span class="on">Lean right</span><span>Right</span><span>Far right</span></div></div>
  </div>
  <p class="note">Selection closely tracks the catalog, so the digest is balanced <em>relative to what it can draw from</em>. The spectrum stops at lean-left &rarr; lean-right <b style="color:var(--ink2);font-weight:600;">by design</b>: the digest holds a factuality floor &mdash; no low-rated sources &mdash; and the political extremes skew low-factuality, so they're excluded on <b style="color:var(--ink2);font-weight:600;">quality, not omitted by accident</b>. A wider spectrum would mean lowering that bar.</p>
</section>"##,
        ship_nums = spectrum_nums(m.shipped_pct, false),
        ship_row = spectrum_row("Shipped", &shipped_aria),
        cat_row = spectrum_row("Catalog", &catalog_aria),
        cat_nums = spectrum_nums(m.catalog_pct, true),
    )
}

fn concentration_section(m: &StatsMetrics, never: &[String], days: u32) -> String {
    let (hhi_cls, hhi_desc) = if m.hhi < 0.15 {
        ("ok", "low — well spread")
    } else if m.hhi < 0.25 {
        ("warn", "moderate")
    } else {
        ("bad", "concentrated")
    };
    let top = m.top_sources.first();
    let tiles = format!(
        "{}{}{}",
        tile(
            &format!(
                r#"{}<span style="color:var(--muted);font-size:18px;"> / {}</span>"#,
                m.sources_used, m.catalog_total
            ),
            "",
            "Sources used",
            &format!("{}% catalog coverage", m.coverage_pct)
        ),
        tile(
            &format!("{:.2}", m.hhi),
            hhi_cls,
            "Concentration (HHI)",
            &format!("{hhi_desc} · eff. {:.0} sources", m.effective_n)
        ),
        tile(
            &top.map(|t| format!("{:.0}%", t.share_pct))
                .unwrap_or_else(|| "—".into()),
            "",
            "Top source share",
            &top.map(|t| escape_html(&t.name)).unwrap_or_default()
        ),
    );
    let shares: String = m
        .top_sources
        .iter()
        .map(|s| {
            format!(
                r#"<div class="share"><span class="sn">{name}</span><span class="track"><span class="fill" style="width:{bar:.0}%"></span></span><span class="pc">{pc:.0}%</span></div>"#,
                name = escape_html(&s.name),
                bar = s.bar_pct,
                pc = s.share_pct,
            )
        })
        .collect();
    let drill = if never.is_empty() {
        String::new()
    } else {
        let codes: String = never
            .iter()
            .map(|s| format!("<code>{}</code>", escape_html(s)))
            .collect::<Vec<_>>()
            .join(", ");
        format!(
            r#"<p class="drill">Never used in {days} days ({n}): {codes}.</p>"#,
            n = never.len()
        )
    };
    format!(
        r#"<section>
  <div class="sec-h"><h2>Concentration &amp; coverage</h2><span class="ct">{total} stories · {days} days</span></div>
  <div class="stats">{tiles}</div>
  <div class="shares">{shares}</div>
  {drill}
</section>"#,
        total = m.total_shipped,
    )
}

fn source_health_section(data: &StatsData) -> String {
    let names = source_names();
    let mut n_ok = 0;
    let mut n_warn = 0;
    let mut n_bad = 0;
    let mut rows = String::new();
    // worst first — surface problems at the top
    let mut health = data.source_health.clone();
    health.sort_by(|a, b| a.success_rate_pct.total_cmp(&b.success_rate_pct));
    for h in &health {
        let (cls, ic) = rag(h.success_rate_pct);
        match cls {
            "ok" => n_ok += 1,
            "warn" => n_warn += 1,
            _ => n_bad += 1,
        }
        let name = names
            .get(&h.source_id)
            .cloned()
            .unwrap_or_else(|| h.source_id.clone());
        rows.push_str(&format!(
            r#"<tr><td class="src">{name}</td><td class="n">{fetches}</td><td class="rate-cell"><span class="rate {cls}"><span class="ic">{ic}</span>{rate:.0}%</span></td></tr>"#,
            name = escape_html(&name),
            fetches = h.total_fetches,
            rate = h.success_rate_pct,
        ));
    }
    if rows.is_empty() {
        rows.push_str(
            r#"<tr><td class="src" colspan="3">No fetch records in this window.</td></tr>"#,
        );
    }
    format!(
        r#"<section>
  <div class="sec-h"><h2>Source health</h2><span class="ct">{n_ok} healthy · {n_warn} degraded · {n_bad} down</span></div>
  <div class="tbl-wrap"><table><thead><tr><th scope="col">Source</th><th scope="col" class="n">Fetches</th><th scope="col" class="n">Success</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>"#
    )
}

fn cost_section(data: &StatsData) -> String {
    let c = &data.cost;
    let per_run = if c.runs > 0 {
        c.cost_total / c.runs as f64
    } else {
        0.0
    };
    let per_sub = if c.recipients_latest > 0 {
        per_run / c.recipients_latest as f64
    } else {
        0.0
    };
    let per_story = if c.kept_total > 0 {
        c.cost_total / c.kept_total as f64
    } else {
        0.0
    };
    let tiles = format!(
        "{}{}{}",
        tile(
            &format!("${per_run:.2}"),
            "",
            "Cost / run",
            "API list-price equiv."
        ),
        tile(
            &format!("${per_sub:.3}"),
            "",
            "Cost / subscriber",
            &format!("{} recipients", c.recipients_latest)
        ),
        tile(
            &format!("${per_story:.3}"),
            "",
            "Cost / story",
            "kept per run"
        ),
    );
    let mut rows = String::new();
    for r in &data.recent_runs {
        let cost = r
            .api_cost_usd
            .map(|v| format!("${v:.2}"))
            .unwrap_or_else(|| "—".into());
        rows.push_str(&format!(
            r#"<tr><td class="time">{when}</td><td class="n">{kept}</td><td class="n">{rcpt}</td><td class="n">{cost}</td></tr>"#,
            when = escape_html(r.run_at.get(0..16).unwrap_or(&r.run_at)),
            kept = r.articles_kept,
            rcpt = r.articles_emailed,
        ));
    }
    if rows.is_empty() {
        rows.push_str(
            r#"<tr><td class="time" colspan="4">No completed runs in this window.</td></tr>"#,
        );
    }
    format!(
        r#"<section>
  <div class="sec-h"><h2>Cost &amp; reach</h2><span class="ct">per run</span></div>
  <div class="stats">{tiles}</div>
  <div class="tbl-wrap"><table><thead><tr><th scope="col">Filed (UTC)</th><th scope="col" class="n">Kept</th><th scope="col" class="n">Recipients</th><th scope="col" class="n">Cost</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>"#
    )
}

fn dedup_section(data: &StatsData) -> String {
    let filtered = data
        .dedup_stats
        .as_ref()
        .map(|d| d.filtered_count)
        .unwrap_or(0);
    let per_run = if data.cost.runs > 0 {
        filtered as f64 / data.cost.runs as f64
    } else {
        0.0
    };
    format!(
        r##"<section>
  <div class="sec-h"><h2>Dedup</h2><span class="ct">TF-IDF title filter</span></div>
  <div class="stats">{tile}</div>
  <div class="planned">
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;"><span class="planned-badge">Planned</span><span style="font-family:var(--mono); font-size:11px; color:var(--muted);">precision · recall · F1 + threshold sweep</span></div>
    <p class="note" style="margin:0;">The old <em>avg-similarity</em> metric was <b style="color:var(--ink2);font-weight:600;">cut</b> — it's blind to false negatives and rewards false positives, so it can't tell a good threshold from a broken one. The real signal needs a labelled ~200-pair set near the boundary, scored with B-Cubed F.</p>
  </div>
</section>"##,
        tile = tile(
            &filtered.to_string(),
            "",
            "Filtered this period",
            &format!("~{per_run:.1} / run")
        ),
    )
}

/// The two fully-planned roadmap blocks (no fake numbers — placeholders + the data-need note).
const PLANNED_SECTIONS: &str = r##"<section>
  <div class="sec-h"><h2>Freshness</h2><span class="planned-badge">Planned</span><span class="ct">publish &rarr; send</span></div>
  <div class="planned">
    <div class="stats"><span class="st"><span class="v">—</span><span class="l">Median lag</span></span><span class="st"><span class="v">—</span><span class="l">p90 lag · must-know</span></span></div>
    <p class="note" style="margin-top:14px;">Timeliness is a daily digest's whole value. Cheap to add: the article <code>published</code> time already flows through the pipeline &mdash; it just needs persisting as one column on <code>shown_narratives</code>.</p>
  </div>
</section>
<section>
  <div class="sec-h"><h2>Delivery &amp; engagement</h2><span class="planned-badge">Planned</span><span class="ct">needs Resend webhooks</span></div>
  <div class="planned">
    <div class="stats"><span class="st"><span class="v">—</span><span class="l">Bounce rate</span></span><span class="st"><span class="v">—</span><span class="l">Complaint rate</span></span><span class="st"><span class="v">—</span><span class="l">Click-through</span></span><span class="st"><span class="v">—</span><span class="l">Unsubscribe rate</span></span></div>
    <p class="note" style="margin-top:14px;">A hard deliverability floor you currently can't see. Needs a Resend webhook endpoint + an events table. Raw <em>opens</em> deliberately excluded — MPP inflates them; raw <em>subscriber count</em> excluded — vanity.</p>
  </div>
</section>"##;

fn period_toggle(days: u32) -> String {
    let opt = |d: u32, label: &str| {
        let cur = if d == days { "true" } else { "false" };
        format!(r#"<a href="?days={d}" aria-current="{cur}">{label}</a>"#)
    };
    format!(
        r#"<div class="toolbar"><div class="seg" role="group" aria-label="Reporting period">{}{}{}</div></div>"#,
        opt(7, "7 days"),
        opt(30, "30 days"),
        opt(90, "90 days"),
    )
}

pub fn render_stats(p: &StatsParams) -> String {
    let m = p.metrics;
    let head = chrome::page_head(
        p.title,
        "How the digest is performing: balance, source health, cost, and coverage.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        STATS_CSS,
    );
    let masthead = chrome::sub_masthead(
        p.home_url,
        p.brand_html,
        "Stats",
        &format!("Editorial health &middot; {}", period_label(p.days)),
        &format!(
            "<b>{}</b> runs &middot; <b>{}</b> subscribers &middot; <b>${:.2}</b> API-equiv.",
            p.data.cost.runs, p.data.cost.recipients_latest, p.data.cost.cost_total
        ),
    );

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    {masthead}
    {toggle}
    <main id="main">
    {balance}
    {concentration}
    {health}
    {cost}
    {dedup}
    {planned}
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        toggle = period_toggle(p.days),
        balance = balance_section(m),
        concentration = concentration_section(m, &p.data.never_selected, p.days),
        health = source_health_section(p.data),
        cost = cost_section(p.data),
        dedup = dedup_section(p.data),
        planned = PLANNED_SECTIONS,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stats::{CostSummary, DedupStats, DigestRun, SourceHealth, SourceUsage};

    fn sample_data() -> StatsData {
        StatsData {
            period_days: 30,
            source_health: vec![
                SourceHealth {
                    source_id: "reuters".into(),
                    total_fetches: 210,
                    successes: 208,
                    success_rate_pct: 99.0,
                },
                SourceHealth {
                    source_id: "deutsche_welle".into(),
                    total_fetches: 210,
                    successes: 189,
                    success_rate_pct: 90.0,
                },
                SourceHealth {
                    source_id: "kyiv_independent".into(),
                    total_fetches: 210,
                    successes: 143,
                    success_rate_pct: 68.0,
                },
            ],
            source_usage: vec![
                SourceUsage {
                    source_id: "reuters".into(),
                    tier: "must_know".into(),
                    count: 40,
                },
                SourceUsage {
                    source_id: "bbc_world".into(),
                    tier: "must_know".into(),
                    count: 30,
                },
                SourceUsage {
                    source_id: "globe_and_mail".into(),
                    tier: "should_know".into(),
                    count: 10,
                },
            ],
            recent_runs: vec![DigestRun {
                run_at: "2026-07-03 10:24:00".into(),
                articles_kept: 312,
                articles_emailed: 47,
                api_cost_usd: Some(2.81),
            }],
            dedup_stats: Some(DedupStats {
                filtered_count: 84,
                avg_similarity: 0.0,
                min_similarity: 0.0,
                max_similarity: 0.0,
            }),
            never_selected: vec!["tass_english".into(), "rt_world".into()],
            cost: CostSummary {
                runs: 30,
                cost_total: 79.10,
                kept_total: 9000,
                recipients_latest: 47,
            },
        }
    }

    fn params<'a>(data: &'a StatsData, metrics: &'a StatsMetrics) -> StatsParams<'a> {
        StatsParams {
            title: "Stats",
            brand_html: "News <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://example.com/stats",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            days: 30,
            data,
            metrics,
        }
    }

    #[test]
    fn renders_all_sections_and_period_toggle() {
        let data = sample_data();
        let m = crate::stats::compute_metrics(&data);
        let html = render_stats(&params(&data, &m));
        assert_eq!(html.matches("<h1").count(), 1);
        for h in [
            "Balance",
            "Concentration",
            "Source health",
            "Cost",
            "Dedup",
            "Freshness",
            "Delivery",
        ] {
            assert!(
                html.contains(&format!("<h2>{h}")) || html.contains(&format!(">{h}")),
                "missing {h}"
            );
        }
        // period toggle: 30 days current
        assert!(html.contains(r#"<a href="?days=30" aria-current="true">30 days</a>"#));
        assert!(html.contains(r#"<a href="?days=7" aria-current="false">7 days</a>"#));
        // RAG: worst-first ordering puts the 68% "down" row before the 99% "ok" row
        assert!(html.find("68%").unwrap() < html.find("99%").unwrap());
        assert!(html.contains(r#"<span class="rate bad">"#));
        // planned tiles present, no fake numbers
        assert_eq!(html.matches(r#"class="planned-badge">Planned"#).count(), 3);
        // masthead statline
        assert!(html.contains("<b>30</b> runs &middot; <b>47</b> subscribers"));
    }

    #[test]
    fn cost_ratios_computed() {
        let data = sample_data();
        let m = crate::stats::compute_metrics(&data);
        let html = render_stats(&params(&data, &m));
        // cost/run = 79.10 / 30 = 2.64 (2dp)
        assert!(html.contains("$2.64"));
    }
}
