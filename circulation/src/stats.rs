use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Html,
};
use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;

use crate::AppState;
use crate::handlers::{brand_html, sub_chrome};
use crate::routes;
use crate::templates::{StatsParams, render_stats};
use crate::util::log_row_error;

#[derive(Deserialize, Default)]
pub struct StatsQuery {
    pub days: Option<u32>,
}

#[derive(Clone)]
pub struct SourceHealth {
    pub source_id: String,
    pub total_fetches: i64,
    pub successes: i64,
    pub success_rate_pct: f64,
}

#[derive(Clone)]
pub struct SourceUsage {
    pub source_id: String,
    pub tier: String,
    pub count: i64,
}

#[derive(Clone)]
pub struct DigestRun {
    pub run_at: String,
    pub articles_kept: i64,
    pub articles_emailed: i64,
    pub api_cost_usd: Option<f64>,
}

#[derive(Clone)]
pub struct DedupStats {
    pub filtered_count: i64,
    pub avg_similarity: f64,
    pub min_similarity: f64,
    pub max_similarity: f64,
}

/// Period cost aggregate (over the whole window, not just the last-10 `recent_runs`).
#[derive(Clone, Default)]
pub struct CostSummary {
    pub runs: i64,
    pub cost_total: f64,
    /// Articles that survived fetch + dedup and went INTO curation (~550/run). The operational
    /// denominator, not the reader-facing one — a reader never sees most of these.
    pub kept_total: i64,
    /// Stories actually shipped to readers (~16/run). `shown_narratives` holds one row per
    /// SOURCE per story, so the story count is DISTINCT (run_id, headline), not row count.
    pub shipped_total: i64,
    /// Recipients on the most recent run (the current subscriber count) — the cost/subscriber base.
    pub recipients_latest: i64,
}

pub struct StatsData {
    pub period_days: u32,
    pub source_health: Vec<SourceHealth>,
    pub source_usage: Vec<SourceUsage>,
    pub recent_runs: Vec<DigestRun>,
    pub dedup_stats: Option<DedupStats>,
    pub never_selected: Vec<String>,
    pub cost: CostSummary,
}

/// Fetch stats data from database
pub fn fetch_stats_data(db_path: &str, days: u32) -> Result<StatsData, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Source health: success rate per source over last N days
    let source_health: Vec<SourceHealth> = {
        let mut stmt = conn
            .prepare(
                "SELECT source_id,
                        COUNT(*) as total,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
                 FROM source_health
                 WHERE recorded_at >= datetime('now', '-' || ?1 || ' days')
                 GROUP BY source_id
                 ORDER BY source_id",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| {
            let source_id: String = row.get(0)?;
            let total: i64 = row.get(1)?;
            let successes: i64 = row.get(2)?;
            let rate = if total > 0 {
                (successes as f64 / total as f64 * 100.0).round()
            } else {
                0.0
            };
            Ok(SourceHealth {
                source_id,
                total_fetches: total,
                successes,
                success_rate_pct: rate,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "source_health"))
        .collect()
    };

    // Source usage: how often each source appears in digests, by tier
    let source_usage: Vec<SourceUsage> = {
        let mut stmt = conn
            .prepare(
                "SELECT source_id, tier, COUNT(*) as count
                 FROM shown_narratives
                 WHERE source_id IS NOT NULL
                   AND shown_at >= datetime('now', '-' || ?1 || ' days')
                 GROUP BY source_id, tier
                 ORDER BY count DESC",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| {
            Ok(SourceUsage {
                source_id: row.get(0)?,
                tier: row.get(1)?,
                count: row.get(2)?,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "shown_narratives"))
        .collect()
    };

    // Recent runs: last 10 digest runs
    let recent_runs: Vec<DigestRun> = {
        let mut stmt = conn
            .prepare(
                "SELECT dr.run_at, dr.articles_kept, dr.articles_emailed,
                        SUM(ru.api_cost_usd) as total_cost
                 FROM digest_runs dr
                 LEFT JOIN run_usage ru ON ru.run_id = dr.id
                 WHERE dr.completed_at IS NOT NULL
                 GROUP BY dr.id
                 ORDER BY dr.run_at DESC
                 LIMIT 10",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([], |row| {
            Ok(DigestRun {
                run_at: row.get(0)?,
                articles_kept: row.get(1)?,
                articles_emailed: row.get(2)?,
                api_cost_usd: row.get(3)?,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "digest_runs"))
        .collect()
    };

    // Dedup stats: how effective is the TF-IDF filter
    let dedup_stats: Option<DedupStats> = conn
        .query_row(
            "SELECT COUNT(*) as filtered,
                    AVG(similarity) as avg_sim,
                    MIN(similarity) as min_sim,
                    MAX(similarity) as max_sim
             FROM dedup_log
             WHERE logged_at >= datetime('now', '-' || ?1 || ' days')",
            [days],
            |row| {
                let count: i64 = row.get(0)?;
                if count == 0 {
                    Ok(None)
                } else {
                    Ok(Some(DedupStats {
                        filtered_count: count,
                        avg_similarity: row.get(1)?,
                        min_similarity: row.get(2)?,
                        max_similarity: row.get(3)?,
                    }))
                }
            },
        )
        .unwrap_or(None);

    // Never-selected sources: in source_health but not in shown_narratives
    let never_selected: Vec<String> = {
        let mut stmt = conn
            .prepare(
                "SELECT DISTINCT h.source_id
                 FROM source_health h
                 WHERE h.recorded_at >= datetime('now', '-' || ?1 || ' days')
                   AND h.source_id NOT IN (
                       SELECT DISTINCT source_id
                       FROM shown_narratives
                       WHERE source_id IS NOT NULL
                         AND shown_at >= datetime('now', '-' || ?1 || ' days')
                   )
                 ORDER BY h.source_id",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;

        stmt.query_map([days], |row| row.get(0))
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?
            .filter_map(|r| log_row_error(r, "source_health"))
            .collect()
    };

    // Period cost aggregate. Subqueries (not a join) so SUM(articles_kept) isn't multiplied by the
    // per-run `run_usage` row count. `?1` is reused across all windows. recipients = the latest run's
    // emailed count (current subscriber base), independent of the window.
    //
    // shipped_total counts DISTINCT (run_id, headline): `shown_narratives` stores one row per SOURCE
    // per story (~7 rows per story), so COUNT(*) would overstate the story count several-fold. Same
    // headline on two days is two shipped stories, hence run_id in the key.
    let cost: CostSummary = conn
        .query_row(
            "SELECT
               (SELECT COUNT(*) FROM digest_runs
                  WHERE completed_at IS NOT NULL AND run_at >= datetime('now','-'||?1||' days')),
               (SELECT COALESCE(SUM(articles_kept),0) FROM digest_runs
                  WHERE completed_at IS NOT NULL AND run_at >= datetime('now','-'||?1||' days')),
               (SELECT COALESCE(SUM(ru.api_cost_usd),0.0) FROM run_usage ru
                  JOIN digest_runs dr ON dr.id = ru.run_id
                  WHERE dr.completed_at IS NOT NULL AND dr.run_at >= datetime('now','-'||?1||' days')),
               (SELECT COUNT(*) FROM (SELECT DISTINCT sn.run_id, sn.headline
                  FROM shown_narratives sn JOIN digest_runs dr ON dr.id = sn.run_id
                  WHERE dr.completed_at IS NOT NULL AND dr.run_at >= datetime('now','-'||?1||' days'))),
               (SELECT COALESCE(articles_emailed,0) FROM digest_runs
                  WHERE completed_at IS NOT NULL ORDER BY run_at DESC LIMIT 1)",
            [days],
            |row| {
                Ok(CostSummary {
                    runs: row.get(0)?,
                    kept_total: row.get(1)?,
                    cost_total: row.get(2)?,
                    shipped_total: row.get(3)?,
                    recipients_latest: row.get(4).unwrap_or(0),
                })
            },
        )
        .unwrap_or_default();

    Ok(StatsData {
        period_days: days,
        source_usage,
        recent_runs,
        dedup_stats,
        // Filtered HERE, at the data layer, not in the template: /stats.json is advertised as the
        // machine-readable twin of the page, and filtering only the HTML made a transparency
        // surface contradict its own JSON. One parked source, two answers, is worse than either.
        source_health: drop_parked_health(source_health),
        never_selected: drop_parked(never_selected),
        cost,
    })
}

/// A parked source keeps its real fetch failures in the 30-day window. They are true, and they are
/// a decision already taken -- surfacing them as a live fault is the crying-wolf the digest's health
/// ALERT was fixed to stop doing. Dropped once, here, so every consumer of `StatsData` agrees.
fn drop_parked_health(rows: Vec<SourceHealth>) -> Vec<SourceHealth> {
    let parked = parked_source_ids();
    rows.into_iter()
        .filter(|h| !parked.contains(&h.source_id))
        .collect()
}

/// Same reasoning for "never selected in N days": a parked source was not selected because it was
/// not fetched.
fn drop_parked(ids: Vec<String>) -> Vec<String> {
    let parked = parked_source_ids();
    ids.into_iter().filter(|id| !parked.contains(id)).collect()
}

// ─────────────────────── derived metrics (balance / concentration / coverage) ───────────────────────

/// A source's share of shipped narratives, for the concentration bars.
pub struct SourceShare {
    pub name: String,
    /// Absolute share of all shipped narratives (count / total), as a percentage.
    pub share_pct: f64,
    /// Bar length relative to the top source (top = 100), as a percentage.
    pub bar_pct: f64,
}

/// The computed editorial-health metrics for the stats page. All derived from `source_usage`
/// (shipped narratives over the window) joined to `sources.json` (bias/factuality/name) + the cost
/// aggregate — SELECTs over existing data, no new columns.
#[derive(Default)]
pub struct StatsMetrics {
    // Balance (over sources with a KNOWN bias — the l/c/r catalog)
    pub shipped_pct: [i64; 3], // l, c, r — rounded, sum to 100
    pub catalog_pct: [i64; 3],
    pub jsd: f64, // Jensen–Shannon divergence (log2, 0..1), shipped vs catalog
    pub factuality_high_pct: i64, // % of shipped from high/very-high factuality sources
    pub buckets_sourced: i64, // populated spectrum buckets (of 7; only l/c/r can populate)
    // Concentration (over ALL shipped sources)
    /// `shown_narratives` rows in the window = one per SOURCE per story (~7x the story count),
    /// which is the right denominator for source shares but is NOT a story count. Cost per story
    /// uses `CostSummary::shipped_total`.
    pub total_shipped: i64,
    pub hhi: f64,                      // Σ share² (0..1); low = well spread
    pub effective_n: f64,              // 1 / HHI
    pub top_sources: Vec<SourceShare>, // top 5 by count
    // Coverage. BOTH sides are the ACTIVE catalogue: a parked source can still appear in a
    // window that reaches back before it was parked, and counting it in the numerator against an
    // active-only denominator puts coverage_pct over 100.
    pub sources_used: i64, // distinct ACTIVE catalog sources shipped in the window
    pub catalog_total: i64, // active entries in sources.json (parked ones excluded)
    pub coverage_pct: i64,
    // Geographic (source-origin regions, from sources.json `region`)
    pub regions: Vec<(String, i64)>, // (region, shipped count) sorted desc
    pub geo_hhi: f64,                // concentration across region shares
    pub geo_effective: f64,          // effective region count = 1/geo_hhi
}

/// One catalogue row as `/stats` needs it.
///
/// `active` is the split this module cannot do without: the SHIPPED figures describe history and
/// must count a parked source, because it really did ship those rows, while the CATALOG figures
/// describe the shelf we read today and must not. Both are computed here from one map.
struct SourceMeta {
    bucket: char,
    factuality: String,
    name: String,
    region: String,
    active: bool,
}

/// Ids parked with `"active": false` -- in the catalogue, deliberately not fetched.
///
/// The health surfaces are PRESENT tense ("are my feeds working"), so a parked source must not
/// count toward "N down" or "never used": its failures are a decision already taken, and reporting
/// them as a live fault is the same crying-wolf the digest's health ALERT was fixed to stop doing.
/// It self-heals as the 30-day window rolls past the parking date; this makes it immediate.
pub fn parked_source_ids() -> std::collections::HashSet<String> {
    sources_meta()
        .into_iter()
        .filter(|(_, m)| !m.active)
        .map(|(id, _)| id)
        .collect()
}

/// id -> display name, from the compiled-in `sources.json` (for the source-health table).
/// Includes PARKED sources: this names historical rows, which parked ids still appear in.
pub fn source_names() -> HashMap<String, String> {
    sources_meta()
        .into_iter()
        .map(|(id, m)| (id, m.name))
        .collect()
}

/// id -> (bias bucket 'l'|'c'|'r', factuality, display name, origin `region`), from `sources.json`.
/// `region` is an explicit per-source field in sources.json (source-origin vantage, not story location).
fn sources_meta() -> HashMap<String, SourceMeta> {
    fn default_active() -> bool {
        true
    }

    #[derive(serde::Deserialize, Default)]
    struct Raw {
        id: String,
        name: String,
        bias: String,
        factuality: String,
        #[serde(default)]
        region: String,
        #[serde(default = "default_active")]
        active: bool,
    }
    let raw: Vec<Raw> = serde_json::from_str(include_str!("../sources.json")).unwrap_or_default();
    raw.into_iter()
        .map(|s| {
            let bucket = match s.bias.as_str() {
                "far-left" | "left" | "lean-left" => 'l',
                "lean-right" | "right" | "far-right" => 'r',
                _ => 'c',
            };
            let region = if s.region.is_empty() {
                "Global".to_string()
            } else {
                s.region
            };
            (
                s.id,
                SourceMeta {
                    bucket,
                    factuality: s.factuality,
                    name: s.name,
                    region,
                    active: s.active,
                },
            )
        })
        .collect()
}

/// Normalise counts to a probability vector (sums to 1; all-zero -> all-zero).
fn normalize3(v: [i64; 3]) -> [f64; 3] {
    let total: i64 = v.iter().sum();
    if total == 0 {
        return [0.0; 3];
    }
    [
        v[0] as f64 / total as f64,
        v[1] as f64 / total as f64,
        v[2] as f64 / total as f64,
    ]
}

/// KL divergence Σ a·log2(a/b) over a 3-vector, skipping zero terms.
fn kl3(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3)
        .map(|i| {
            if a[i] > 0.0 && b[i] > 0.0 {
                a[i] * (a[i] / b[i]).log2()
            } else {
                0.0
            }
        })
        .sum()
}

/// Jensen–Shannon divergence (log2 -> range 0..1) between two 3-bucket distributions.
fn jsd3(p: [i64; 3], q: [i64; 3]) -> f64 {
    let (p, q) = (normalize3(p), normalize3(q));
    let m = [
        (p[0] + q[0]) / 2.0,
        (p[1] + q[1]) / 2.0,
        (p[2] + q[2]) / 2.0,
    ];
    ((kl3(p, m) + kl3(q, m)) / 2.0).clamp(0.0, 1.0)
}

/// Round a probability vector to integer percentages that sum to exactly 100 (drift into the largest).
fn pct3(v: [i64; 3]) -> [i64; 3] {
    let p = normalize3(v);
    let mut out = [
        (p[0] * 100.0).round() as i64,
        (p[1] * 100.0).round() as i64,
        (p[2] * 100.0).round() as i64,
    ];
    let sum: i64 = out.iter().sum();
    if sum != 0 && sum != 100 {
        // absorb the rounding drift into the largest bucket
        let max_i = (0..3).max_by(|&a, &b| p[a].total_cmp(&p[b])).unwrap();
        out[max_i] += 100 - sum;
    }
    out
}

pub fn compute_metrics(data: &StatsData) -> StatsMetrics {
    let meta = sources_meta();

    // Aggregate shipped counts per source across tiers.
    let mut per_source: HashMap<&str, i64> = HashMap::new();
    for u in &data.source_usage {
        *per_source.entry(u.source_id.as_str()).or_insert(0) += u.count;
    }
    let total_shipped: i64 = per_source.values().sum();

    // Bias mix + factuality + region over sources with known metadata.
    let mut shipped_lcr = [0i64; 3];
    let mut fact_high = 0i64;
    let mut fact_known = 0i64;
    let mut region_counts: HashMap<String, i64> = HashMap::new();
    for (id, &count) in &per_source {
        // Parked sources are NOT skipped here: this describes what the digest SHIPPED, and a
        // source parked today really did ship these rows. Only the catalog-side figures below
        // ask what we read now.
        if let Some(SourceMeta {
            bucket,
            factuality,
            region,
            ..
        }) = meta.get(*id)
        {
            match bucket {
                'l' => shipped_lcr[0] += count,
                'c' => shipped_lcr[1] += count,
                _ => shipped_lcr[2] += count,
            }
            fact_known += count;
            if matches!(factuality.as_str(), "high" | "very-high") {
                fact_high += count;
            }
            *region_counts.entry(region.clone()).or_insert(0) += count;
        }
    }
    // Geographic: region shares + concentration (geo-HHI), sorted desc.
    let geo_total: i64 = region_counts.values().sum();
    let mut regions: Vec<(String, i64)> = region_counts.into_iter().collect();
    regions.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    let geo_hhi: f64 = if geo_total > 0 {
        regions
            .iter()
            .map(|(_, c)| {
                let s = *c as f64 / geo_total as f64;
                s * s
            })
            .sum()
    } else {
        0.0
    };

    // Catalog bias distribution -- the shelf we read TODAY, so parked sources are excluded. The
    // shipped side above deliberately keeps them, so for as long as a parked source sits inside
    // the window `jsd` compares against a slightly different shelf than shipped it. That
    // self-heals as the window rolls past the parking date.
    // Counting them would put a feed that can never be fetched again into the denominator of
    // `coverage_pct`, pinning it below 100 forever and pre-spending the one number that would
    // otherwise say "a source stopped shipping".
    let mut catalog_lcr = [0i64; 3];
    for m in meta.values().filter(|m| m.active) {
        match m.bucket {
            'l' => catalog_lcr[0] += 1,
            'c' => catalog_lcr[1] += 1,
            _ => catalog_lcr[2] += 1,
        }
    }

    // Concentration: HHI over all shipped sources.
    let hhi: f64 = if total_shipped > 0 {
        per_source
            .values()
            .map(|&c| {
                let s = c as f64 / total_shipped as f64;
                s * s
            })
            .sum()
    } else {
        0.0
    };

    // Top 5 sources by count.
    let mut ranked: Vec<(&str, i64)> = per_source.iter().map(|(&k, &v)| (k, v)).collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(b.0)));
    let top_count = ranked.first().map(|(_, c)| *c).unwrap_or(0);
    let top_sources: Vec<SourceShare> = ranked
        .iter()
        .take(5)
        .map(|(id, count)| SourceShare {
            name: meta
                .get(*id)
                .map(|m| m.name.clone())
                .unwrap_or_else(|| (*id).to_string()),
            share_pct: if total_shipped > 0 {
                *count as f64 / total_shipped as f64 * 100.0
            } else {
                0.0
            },
            bar_pct: if top_count > 0 {
                *count as f64 / top_count as f64 * 100.0
            } else {
                0.0
            },
        })
        .collect();

    // Coverage: distinct ACTIVE catalog sources shipped vs the active catalog. The `active`
    // filter is not decoration -- the 90-day window still reaches rows from before a source was
    // parked, and `contains_key` alone would count 38 against a catalog of 37, rendering
    // "38 / 37 Sources used - 103% catalog coverage".
    let sources_used = per_source
        .keys()
        .filter(|id| meta.get(**id).is_some_and(|m| m.active))
        .count() as i64;
    let catalog_total = meta.values().filter(|m| m.active).count() as i64;

    StatsMetrics {
        shipped_pct: pct3(shipped_lcr),
        catalog_pct: pct3(catalog_lcr),
        jsd: jsd3(shipped_lcr, catalog_lcr),
        factuality_high_pct: if fact_known > 0 {
            (fact_high as f64 / fact_known as f64 * 100.0).round() as i64
        } else {
            0
        },
        buckets_sourced: shipped_lcr.iter().filter(|&&c| c > 0).count() as i64,
        total_shipped,
        hhi,
        effective_n: if hhi > 0.0 { 1.0 / hhi } else { 0.0 },
        top_sources,
        sources_used,
        catalog_total,
        coverage_pct: if catalog_total > 0 {
            (sources_used as f64 / catalog_total as f64 * 100.0).round() as i64
        } else {
            0
        },
        regions,
        geo_hhi,
        geo_effective: if geo_hhi > 0.0 { 1.0 / geo_hhi } else { 0.0 },
    }
}

#[cfg(test)]
mod metrics_tests {
    use super::*;

    #[test]
    fn parked_sources_leave_the_catalog_but_stay_in_the_history() {
        // The two halves of /stats read the same file for opposite reasons. `catalog_total` and
        // `catalog_pct` describe the shelf we read TODAY -- counting a parked feed there pins
        // `coverage_pct` below 100 forever and quietly spends the signal that would say a source
        // stopped shipping. `source_names` and the shipped figures describe history, where a
        // parked id still appears, so they must keep it.
        let meta = sources_meta();
        let parked: Vec<&String> = meta
            .iter()
            .filter(|(_, m)| !m.active)
            .map(|(id, _)| id)
            .collect();
        assert!(
            !parked.is_empty(),
            "no parked source in sources.json -- this test is guarding nothing"
        );
        for id in &parked {
            assert!(
                source_names().contains_key(*id),
                "{id} is parked and the source-health table can no longer name it"
            );
        }
        let active = meta.values().filter(|m| m.active).count() as i64;
        assert_eq!(
            compute_metrics(&data_with(vec![])).catalog_total,
            active,
            "catalog_total must count the feeds we still read, not the parked ones"
        );
    }

    #[test]
    fn coverage_cannot_exceed_100_when_the_window_predates_the_parking() {
        // The 90-day window still reaches rows a source shipped BEFORE it was parked. Counting
        // those into a numerator while the denominator is active-only rendered "38 / 37 Sources
        // used, 103% catalog coverage" -- and only at days=90, which is why a spot check at the
        // default window missed it. Feed one shipped row for EVERY catalogue id, parked included.
        let usage: Vec<SourceUsage> = sources_meta()
            .keys()
            .map(|id| SourceUsage {
                source_id: id.clone(),
                tier: "must_know".to_string(),
                count: 1,
            })
            .collect();
        let m = compute_metrics(&data_with(usage));
        assert!(
            m.sources_used <= m.catalog_total,
            "sources_used {} exceeds catalog_total {}",
            m.sources_used,
            m.catalog_total
        );
        assert!(
            m.coverage_pct <= 100,
            "coverage_pct {} exceeds 100",
            m.coverage_pct
        );
    }

    #[test]
    fn jsd_is_zero_for_identical_and_one_for_disjoint() {
        assert!((jsd3([3, 5, 2], [30, 50, 20])).abs() < 1e-9); // same distribution
        assert!((jsd3([1, 0, 0], [0, 1, 0]) - 1.0).abs() < 1e-9); // disjoint -> 1
        let partial = jsd3([1, 1, 0], [0, 1, 1]);
        assert!(partial > 0.0 && partial < 1.0);
    }

    #[test]
    fn pct3_sums_to_100_absorbing_drift() {
        assert_eq!(pct3([1, 1, 1]).iter().sum::<i64>(), 100); // 33/33/34
        assert_eq!(pct3([41, 51, 8]).iter().sum::<i64>(), 100);
        assert_eq!(pct3([0, 0, 0]), [0, 0, 0]);
    }

    fn usage(rows: &[(&str, i64)]) -> Vec<SourceUsage> {
        rows.iter()
            .map(|(id, c)| SourceUsage {
                source_id: (*id).to_string(),
                tier: "must_know".into(),
                count: *c,
            })
            .collect()
    }

    fn data_with(usage: Vec<SourceUsage>) -> StatsData {
        StatsData {
            period_days: 30,
            source_health: vec![],
            source_usage: usage,
            recent_runs: vec![],
            dedup_stats: None,
            never_selected: vec![],
            cost: CostSummary::default(),
        }
    }

    #[test]
    fn hhi_and_effective_n_for_known_shares() {
        // two real catalog sources, 3:1 split -> shares .75/.25 -> HHI .625, eff-N 1.6
        let m = compute_metrics(&data_with(usage(&[("reuters", 3), ("bbc_world", 1)])));
        assert!((m.hhi - 0.625).abs() < 1e-9);
        assert!((m.effective_n - 1.6).abs() < 1e-9);
        assert_eq!(m.total_shipped, 4);
        assert!((m.top_sources[0].share_pct - 75.0).abs() < 1e-9); // reuters top: 3/4
    }

    #[test]
    fn balance_buckets_and_factuality_from_real_sources_json() {
        // Per MBFC: al_jazeera=lean-left/mixed, bbc_world=center/mostly-factual,
        // globe_and_mail=lean-right/high.
        let m = compute_metrics(&data_with(usage(&[
            ("al_jazeera", 2),
            ("bbc_world", 5),
            ("globe_and_mail", 3),
        ])));
        assert_eq!(m.shipped_pct, [20, 50, 30]); // 2/5/3 of 10 -> l, c, r
        assert_eq!(m.buckets_sourced, 3);
        assert_eq!(m.shipped_pct.iter().sum::<i64>(), 100);
        // catalog is the full shelf; must be non-empty in all buckets present in sources.json
        assert!(m.catalog_total > 0);
        assert_eq!(m.sources_used, 3);
        // Only globe_and_mail clears high/very-high -> 3 of 10.
        assert_eq!(m.factuality_high_pct, 30);
    }

    #[test]
    fn factuality_excludes_non_high_sources() {
        // hacker_news is "unrated"; it should count toward the base but not the high numerator.
        let m = compute_metrics(&data_with(usage(&[("reuters", 3), ("hacker_news", 1)])));
        // 3 of 4 shipped from a high-factuality source -> 75%
        assert_eq!(m.factuality_high_pct, 75);
    }

    #[test]
    fn factuality_metric_excludes_mostly_factual() {
        // Deliberate: the metric is labelled "high/very-high", and MBFC's "Mostly Factual" is a
        // rung below High. Counting it would keep the headline number flattering by redefining
        // the label rather than by sourcing better, so bbc_world (mostly-factual) scores 0 here.
        let m = compute_metrics(&data_with(usage(&[("bbc_world", 4)])));
        assert_eq!(m.factuality_high_pct, 0);
    }
}

/// Stats JSON endpoint
pub async fn stats_json(
    State(state): State<Arc<AppState>>,
    Query(query): Query<StatsQuery>,
) -> Result<axum::Json<serde_json::Value>, (StatusCode, String)> {
    let days = query.days.unwrap_or(30);
    let data = fetch_stats_data(&state.db_path, days)?;
    Ok(axum::Json(stats_value(&data)))
}

/// The `/stats.json` document. Shared with the MCP `get_stats` tool so both doors serve the
/// same shape.
pub fn stats_value(data: &StatsData) -> serde_json::Value {
    let source_health: Vec<serde_json::Value> = data
        .source_health
        .iter()
        .map(|h| {
            serde_json::json!({
                "source_id": h.source_id,
                "total_fetches": h.total_fetches,
                "successes": h.successes,
                "success_rate_pct": h.success_rate_pct
            })
        })
        .collect();

    let source_usage: Vec<serde_json::Value> = data
        .source_usage
        .iter()
        .map(|u| {
            serde_json::json!({
                "source_id": u.source_id,
                "tier": u.tier,
                "count": u.count
            })
        })
        .collect();

    let recent_runs: Vec<serde_json::Value> = data
        .recent_runs
        .iter()
        .map(|r| {
            serde_json::json!({
                "run_at": r.run_at,
                "articles_kept": r.articles_kept,
                "articles_emailed": r.articles_emailed,
                "api_cost_usd": r.api_cost_usd
            })
        })
        .collect();

    let dedup_stats = data.dedup_stats.as_ref().map(|d| {
        serde_json::json!({
            "filtered_count": d.filtered_count,
            "avg_similarity": d.avg_similarity,
            "min_similarity": d.min_similarity,
            "max_similarity": d.max_similarity
        })
    });

    serde_json::json!({
        "period_days": data.period_days,
        "source_health": source_health,
        "source_usage": source_usage,
        "recent_runs": recent_runs,
        "dedup_stats": dedup_stats,
        "never_selected": data.never_selected
    })
}

/// Stats HTML dashboard
pub async fn stats_html(
    State(state): State<Arc<AppState>>,
    Query(query): Query<StatsQuery>,
) -> Result<Html<String>, (StatusCode, String)> {
    // Allowlist the period toggle to 7/30/90 (default 30); never interpolate an arbitrary window.
    let days = match query.days {
        Some(7) => 7,
        Some(90) => 90,
        _ => 30,
    };
    let data = fetch_stats_data(&state.db_path, days)?;
    let metrics = compute_metrics(&data);
    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "stats",
        crate::routes::STATS,
        "Balance = shipped source-bias mix vs. the catalog. Cost is API list-price equiv., not billed spend.",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    let html = render_stats(&StatsParams {
        title: &state.digest_name,
        brand_html: &brand,
        home_url: "/",
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        days,
        data: &data,
        metrics: &metrics,
    });
    Ok(Html(html))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static COUNTER: AtomicU32 = AtomicU32::new(0);

    /// Throwaway on-disk sqlite with the stats-relevant schema (mirrors the archive/thread test
    /// fixtures). Columns match the production tables the queries touch; caller seeds rows via
    /// `inserts` (an SQL batch, using `datetime('now', ...)` to place rows in/out of the window).
    fn seed_db(inserts: &str) -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir()
            .join(format!("stats_test_{}_{n}.db", std::process::id()))
            .to_string_lossy()
            .into_owned();
        let _ = std::fs::remove_file(&path);
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE source_health (source_id TEXT NOT NULL, success INTEGER NOT NULL, recorded_at DATETIME);
             CREATE TABLE shown_narratives (headline TEXT, tier TEXT, source_id TEXT, shown_at DATETIME, run_id INTEGER);
             CREATE TABLE digest_runs (id INTEGER PRIMARY KEY, run_at DATETIME, articles_kept INTEGER, articles_emailed INTEGER, completed_at DATETIME);
             CREATE TABLE run_usage (run_id INTEGER, api_cost_usd REAL);
             CREATE TABLE dedup_log (similarity REAL, logged_at DATETIME);",
        )
        .unwrap();
        if !inserts.is_empty() {
            conn.execute_batch(inserts).unwrap();
        }
        drop(conn);
        path
    }

    #[test]
    fn happy_path_aggregates_across_the_window() {
        // In-window rows use datetime('now'); the '-40 days' rows must be excluded by the 30-day window.
        let path = seed_db(
            "INSERT INTO source_health (source_id, success, recorded_at) VALUES
                ('bbc', 1, datetime('now')),
                ('bbc', 1, datetime('now')),
                ('bbc', 0, datetime('now')),
                ('bbc', 0, datetime('now','-40 days')),   -- out of window: must not count
                ('reuters', 1, datetime('now')),
                ('dead_feed', 0, datetime('now'));         -- fetched but never selected

             INSERT INTO shown_narratives (headline, tier, source_id, shown_at) VALUES
                ('h1', 'must_know', 'bbc', datetime('now')),
                ('h2', 'must_know', 'bbc', datetime('now')),
                ('h3', 'should_know', 'reuters', datetime('now')),
                ('h4', 'must_know', NULL, datetime('now')),          -- null source_id: excluded from usage
                ('h5', 'must_know', 'bbc', datetime('now','-40 days')); -- out of window: excluded

             INSERT INTO digest_runs (id, run_at, articles_kept, articles_emailed, completed_at) VALUES
                (1, '2026-07-01T10:00:00', 10, 8, '2026-07-01T10:05:00'),
                (2, '2026-07-02T10:00:00', 5, 5, '2026-07-02T10:05:00'),
                (3, '2026-07-03T10:00:00', 7, 0, NULL);              -- not completed: excluded

             INSERT INTO run_usage (run_id, api_cost_usd) VALUES
                (1, 1.0),
                (1, 0.5);                                            -- run 2 has no usage rows

             INSERT INTO dedup_log (similarity, logged_at) VALUES
                (0.4, datetime('now')),
                (0.6, datetime('now')),
                (0.8, datetime('now')),
                (0.99, datetime('now','-40 days'));                 -- out of window: excluded",
        );

        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert_eq!(data.period_days, 30);

        // Source health: grouped per source, ordered by source_id; rate is round(successes/total*100).
        assert_eq!(data.source_health.len(), 3);
        let bbc = &data.source_health[0];
        assert_eq!(bbc.source_id, "bbc");
        assert_eq!(bbc.total_fetches, 3); // the -40d row is excluded
        assert_eq!(bbc.successes, 2);
        assert_eq!(bbc.success_rate_pct, 67.0); // round(2/3*100)
        let dead = &data.source_health[1];
        assert_eq!(dead.source_id, "dead_feed");
        assert_eq!(dead.success_rate_pct, 0.0);
        assert_eq!(data.source_health[2].source_id, "reuters");
        assert_eq!(data.source_health[2].success_rate_pct, 100.0);

        // Source usage: non-null source_id in window, grouped by (source,tier), ordered by count DESC.
        assert_eq!(data.source_usage.len(), 2);
        assert_eq!(data.source_usage[0].source_id, "bbc");
        assert_eq!(data.source_usage[0].tier, "must_know");
        assert_eq!(data.source_usage[0].count, 2);
        assert_eq!(data.source_usage[1].source_id, "reuters");
        assert_eq!(data.source_usage[1].count, 1);

        // Recent runs: completed only, newest first; api_cost is SUM(run_usage) or None when absent.
        assert_eq!(data.recent_runs.len(), 2); // run 3 excluded (completed_at NULL)
        assert_eq!(data.recent_runs[0].run_at, "2026-07-02T10:00:00");
        assert_eq!(data.recent_runs[0].articles_kept, 5);
        assert_eq!(data.recent_runs[0].api_cost_usd, None); // no run_usage rows -> SUM is NULL
        assert_eq!(data.recent_runs[1].run_at, "2026-07-01T10:00:00");
        assert_eq!(data.recent_runs[1].articles_emailed, 8);
        assert_eq!(data.recent_runs[1].api_cost_usd, Some(1.5)); // 1.0 + 0.5

        // Dedup: aggregates over in-window rows only.
        let d = data.dedup_stats.expect("dedup stats present");
        assert_eq!(d.filtered_count, 3);
        assert!((d.avg_similarity - 0.6).abs() < 1e-9);
        assert_eq!(d.min_similarity, 0.4);
        assert_eq!(d.max_similarity, 0.8);

        // Never-selected: in source_health window but absent from shown_narratives window.
        assert_eq!(data.never_selected, vec!["dead_feed".to_string()]);
    }

    #[test]
    fn parked_sources_are_dropped_from_the_health_surfaces_at_the_data_layer() {
        // Filtered in fetch_stats_data, NOT in the template, so /stats and /stats.json cannot
        // disagree -- the JSON is advertised as the machine-readable twin of the page. Uses the
        // REAL sources.json, so un-parking a source without revisiting this fails here.
        let parked: Vec<String> = parked_source_ids().into_iter().collect();
        assert!(
            !parked.is_empty(),
            "no parked source in sources.json -- this test is guarding nothing"
        );
        let health: Vec<SourceHealth> = parked
            .iter()
            .map(|id| SourceHealth {
                source_id: id.clone(),
                total_fetches: 30,
                successes: 0,
                success_rate_pct: 0.0,
            })
            .collect();
        assert!(
            drop_parked_health(health).is_empty(),
            "a parked source's 30 days of failures still count toward the health rollup"
        );
        assert!(
            drop_parked(parked.clone()).is_empty(),
            "a parked source still reads as 'never selected'"
        );

        // And an ordinary source is untouched by the filter.
        let live = vec![SourceHealth {
            source_id: "reuters".into(),
            total_fetches: 30,
            successes: 30,
            success_rate_pct: 100.0,
        }];
        assert_eq!(drop_parked_health(live).len(), 1);
    }

    /// The cost aggregate is the page's public unit-economics claim, and `query_row` here is
    /// `unwrap_or_default()` — a broken SELECT would show zeroes rather than fail. This pins every
    /// field, and above all that `shipped_total` counts STORIES (distinct headline per run), not
    /// `shown_narratives` rows (one per source per story) and not `articles_kept` (the ~35x-larger
    /// ingest count the page used to divide by while calling the result "Cost / story").
    #[test]
    fn cost_aggregate_counts_shipped_stories_not_source_rows_or_ingested_articles() {
        let path = seed_db(
            "INSERT INTO digest_runs (id, run_at, articles_kept, articles_emailed, completed_at) VALUES
                (1, datetime('now','-2 days'), 500, 9,  datetime('now','-2 days')),
                (2, datetime('now','-1 days'), 300, 11, datetime('now','-1 days')),
                (3, datetime('now'),           700, 0,  NULL),                   -- running: excluded
                (4, datetime('now','-40 days'), 900, 5, datetime('now','-40 days')); -- out of window

             INSERT INTO run_usage (run_id, api_cost_usd) VALUES
                (1, 1.0), (1, 0.5),
                (2, 2.0),
                (3, 9.0),                                    -- incomplete run: excluded
                (4, 99.0);                                   -- out of window: excluded

             -- Run 1 ships 2 stories from 4 sources; run 2 ships 1 story, re-running run 1's
             -- headline (a second shipped story on a second day, not a duplicate to collapse).
             INSERT INTO shown_narratives (headline, tier, source_id, shown_at, run_id) VALUES
                ('story A', 'must_know',   'bbc',     datetime('now','-2 days'), 1),
                ('story A', 'must_know',   'reuters', datetime('now','-2 days'), 1),
                ('story A', 'must_know',   'ap',      datetime('now','-2 days'), 1),
                ('story B', 'should_know', 'bbc',     datetime('now','-2 days'), 1),
                ('story A', 'must_know',   'bbc',     datetime('now','-1 days'), 2),
                ('story C', 'must_know',   'ap',      datetime('now','-1 days'), 3),  -- incomplete run
                ('story D', 'must_know',   'ap',      datetime('now','-40 days'), 4); -- out of window",
        );

        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();
        let c = &data.cost;

        assert_eq!(c.runs, 2); // completed and in-window only
        assert!((c.cost_total - 3.5).abs() < 1e-9); // 1.0 + 0.5 + 2.0
        assert_eq!(c.kept_total, 800); // 500 + 300 -- articles INTO curation
        assert_eq!(c.shipped_total, 3); // (1,'story A'), (1,'story B'), (2,'story A')
        assert_eq!(c.recipients_latest, 11); // latest completed run's emailed count

        // The three denominators must stay distinct; collapsing any pair is the original defect.
        assert_ne!(c.shipped_total, 5); // 5 = in-window shown_narratives ROWS (per source, not per story)
        assert_ne!(c.shipped_total, c.kept_total);
        assert!(
            (c.cost_total / c.shipped_total as f64) > 10.0 * (c.cost_total / c.kept_total as f64),
            "cost per shipped story must be far above cost per ingested article"
        );
    }

    #[test]
    fn empty_db_yields_zeroes_and_no_dedup() {
        let path = seed_db("");
        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert!(data.source_health.is_empty());
        assert!(data.source_usage.is_empty());
        assert!(data.recent_runs.is_empty());
        assert!(data.never_selected.is_empty());
        assert!(data.dedup_stats.is_none()); // COUNT(*)=0 -> None (never reads the NULL AVG/MIN/MAX)
    }

    #[test]
    fn dedup_out_of_window_returns_none_not_panic() {
        // Rows exist but all fall outside the window: COUNT(*) is 0, so AVG/MIN/MAX come back NULL.
        // The null-guard must return None rather than try to read NULL into f64 (which would error).
        let path = seed_db(
            "INSERT INTO dedup_log (similarity, logged_at) VALUES
                (0.5, datetime('now','-40 days')),
                (0.7, datetime('now','-99 days'));",
        );
        let data = fetch_stats_data(&path, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert!(data.dedup_stats.is_none());
    }
}
