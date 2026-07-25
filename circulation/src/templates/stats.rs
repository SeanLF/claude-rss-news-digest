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
.note{font-family:var(--serif); font-size:14px; color:var(--muted); line-height:1.6; margin:14px 0 0;}
.note code{font-family:var(--mono); font-size:12px; color:var(--ink2);}
.stats{display:flex; flex-wrap:wrap; gap:14px 40px; margin-top:16px;}
.st .v{font-family:var(--serif); font-weight:600; font-size:27px; color:var(--ink); line-height:1; font-variant-numeric:tabular-nums; letter-spacing:-.01em; display:block;}
.st .v.ok{color:var(--ok-ink);} .st .v.warn{color:var(--warn-ink);} .st .v.bad{color:var(--accent-ink);}
.st .l{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin-top:6px; display:block;}
.st .s{font-family:var(--sans); font-size:12px; color:var(--ink2); margin-top:2px; display:block;}
/* balance spectrum */
.bal{margin-top:20px;}
.balrow{display:grid; grid-template-columns:76px 1fr; gap:14px; align-items:start; margin-bottom:14px;}
.balrow .rl{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); text-align:right; padding-top:6px;}
.sbar{display:flex; height:22px; border-radius:5px; overflow:hidden; border:1px solid var(--line);}
.sbar span{display:block;}
.sbar .on-l{background:var(--bias-l);} .sbar .on-c{background:var(--bias-c);} .sbar .on-r{background:var(--bias-r);}
/* each bucket's % sits directly under its portion of the bar (box width == segment width) */
.bkeys{display:flex; margin-top:5px; font-family:var(--mono); font-size:10px; font-variant-numeric:tabular-nums;}
.bkey{text-align:center; white-space:nowrap; overflow:hidden; font-weight:600; color:var(--ink2);}
/* one shared colour-key legend for both bars (redundant colour + name, legible in greyscale) */
.ballegend{display:flex; gap:20px; font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:10px 0 16px 90px;}
.ballegend .k{display:inline-flex; align-items:center; gap:7px;}
.ballegend .sw{width:9px; height:9px; border-radius:2px; flex:none;}
.ballegend .k-l .sw{background:var(--bias-l);} .ballegend .k-c .sw{background:var(--bias-c);} .ballegend .k-r .sw{background:var(--bias-r);}
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
@media (max-width:560px){
  .balrow{grid-template-columns:1fr;} .balrow .rl{text-align:left;}
  .ballegend{margin-left:0;}
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

/// A PROPORTIONAL bias bar — l/c/r segment widths are their percentages (a real shipped-vs-catalog
/// comparison, not a fixed legend). Each bucket's % sits directly under its portion of the bar;
/// the bucket names come from the one shared legend `balance_section` places below both bars.
fn spectrum_row(label: &str, pct: [i64; 3], aria: &str) -> String {
    format!(
        r#"<div class="balrow"><span class="rl">{label}</span><div class="barwrap"><div class="sbar" role="img" aria-label="{aria}"><span class="on-l" style="width:{l}%"></span><span class="on-c" style="width:{c}%"></span><span class="on-r" style="width:{r}%"></span></div><div class="bkeys"><span class="bkey" style="width:{l}%">{l}%</span><span class="bkey" style="width:{c}%">{c}%</span><span class="bkey" style="width:{r}%">{r}%</span></div></div></div>"#,
        l = pct[0],
        c = pct[1],
        r = pct[2],
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
            &format!(
                "<span title=\"Jensen–Shannon divergence: distance from shipped mix to the catalog (0 = identical)\">JSD</span> {:.2} — curation isn't the skew",
                m.jsd
            )
        ),
        tile(
            &format!("{} of 7", m.buckets_sourced),
            "warn",
            "Spectrum buckets sourced",
            "left and right fold into lean-*"
        ),
        tile(
            &format!("{}%", m.factuality_high_pct),
            fact_cls,
            "Shipped ≥ high factuality",
            "MBFC High or Very High"
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
    {ship_row}
    {cat_row}
    <p class="ballegend"><span class="k k-l"><span class="sw"></span>Lean left</span><span class="k k-c"><span class="sw"></span>Centre</span><span class="k k-r"><span class="sw"></span>Lean right</span></p>
  </div>
  <p class="note">The two bars compare the shipped mix against the catalog it draws from — they track closely, so the digest is balanced <em>relative to its shelf</em>. Three buckets appear because left and right <b style="color:var(--ink2);font-weight:600;">fold into lean-left and lean-right</b> here; the catalog does hold Left-rated outlets. What it holds none of is far-left or far-right, and that is a <b style="color:var(--ink2);font-weight:600;">curation choice, not a quality law</b> — plenty of strongly-slanted outlets report accurately.</p>
</section>"##,
        ship_row = spectrum_row("Shipped", m.shipped_pct, &shipped_aria),
        cat_row = spectrum_row("Catalog", m.catalog_pct, &catalog_aria),
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
            "Concentration <span title=\"Herfindahl–Hirschman Index: how concentrated sourcing is; low = well spread\">(HHI)</span>",
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

fn geographic_section(m: &StatsMetrics) -> String {
    if m.regions.is_empty() {
        return String::new();
    }
    let total: i64 = m.regions.iter().map(|(_, c)| c).sum();
    let top = m.regions.first().map(|(_, c)| *c).unwrap_or(1).max(1);
    let rows: String = m
        .regions
        .iter()
        .map(|(name, c)| {
            let pct = if total > 0 {
                *c as f64 / total as f64 * 100.0
            } else {
                0.0
            };
            format!(
                r#"<div class="share"><span class="sn">{name}</span><span class="track"><span class="fill" style="width:{bar:.0}%"></span></span><span class="pc">{pct:.0}%</span></div>"#,
                bar = *c as f64 / top as f64 * 100.0,
            )
        })
        .collect();
    // Round to match the per-region bar rows above ({pct:.0}); integer truncation here would let
    // the same top-region share display as e.g. 52% in the tile but 53% in its own bar row.
    let top_pct = m
        .regions
        .first()
        .map(|(_, c)| {
            if total > 0 {
                (*c as f64 / total as f64 * 100.0).round() as i64
            } else {
                0
            }
        })
        .unwrap_or(0);
    let tiles = format!(
        "{}{}",
        tile(
            &format!("{:.1}", m.geo_effective),
            "",
            "Effective regions",
            // Denominator is the regions actually SHIPPED, not a hardcoded 6 -- adding
            // "S. America" made that literal wrong, and geo_effective could exceed it.
            &format!("of {} · geo-HHI {:.2}", m.regions.len(), m.geo_hhi)
        ),
        tile(
            m.regions.first().map(|(n, _)| n.as_str()).unwrap_or(""),
            "",
            "Top region",
            &format!("{top_pct}% of stories")
        ),
    );
    format!(
        r#"<section>
  <div class="sec-h"><h2>Geographic origin</h2><span class="ct">source vantage, not story location</span></div>
  <div class="stats">{tiles}</div>
  <div class="shares">{rows}</div>
  <p class="note">Region reflects each <em>source's</em> vantage point, not where the story happened. The catalog skews Western — true story-geography would need per-story geo-tagging.</p>
</section>"#
    )
}

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
    {geo}
    {concentration}
    {health}
    {cost}
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
        geo = geographic_section(m),
        concentration = concentration_section(m, &p.data.never_selected, p.days),
        health = source_health_section(p.data),
        cost = cost_section(p.data),
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
            "Geographic origin",
            "Concentration",
            "Source health",
            "Cost",
        ] {
            assert!(
                html.contains(&format!("<h2>{h}")) || html.contains(&format!(">{h}")),
                "missing {h}"
            );
        }
        // dropped sections: dedup + fully-planned tiles are gone
        assert!(!html.contains("planned-badge"));
        assert!(!html.contains("<h2>Dedup"));
        // period toggle: 30 days current
        assert!(html.contains(r#"<a href="?days=30" aria-current="true">30 days</a>"#));
        assert!(html.contains(r#"<a href="?days=7" aria-current="false">7 days</a>"#));
        // RAG: worst-first ordering puts the 68% "down" row before the 99% "ok" row
        assert!(html.find("68%").unwrap() < html.find("99%").unwrap());
        assert!(html.contains(r#"<span class="rate bad">"#));
        // proportional balance bars (widths from the mix, not a fixed 7-cell legend)
        assert!(html.contains(r#"<span class="on-l" style="width:"#));
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
