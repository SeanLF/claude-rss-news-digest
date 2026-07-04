//! Archive data + HTML-over-the-wire load-more.
//!
//! `fetch_archive` is the (validated) data layer; `row_html` renders one issue `<li>` from the SAME
//! source the full index uses; `archive_fragment` (`GET /archive?before=`) serves those `<li>` rows as
//! an HTML fragment for the index's load-more (appended via ~30 lines of vanilla JS over a degradable
//! `<a>`). Deliberately NOT a JSON API — a list only this server consumes shouldn't duplicate its row
//! markup in a client renderer. See `docs/superpowers/specs/2026-07-04-archive-endpoint-index-realdata-design.md`.

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
use crate::util::{escape_html, log_row_error};

const DEFAULT_LIMIT: i64 = 30;
const MAX_LIMIT: i64 = 100;

#[derive(Debug, Clone, PartialEq)]
pub struct IssueRow {
    /// Publication date (YYYY-MM-DD), the row's stable cursor.
    pub date: String,
    /// Sequential issue number, oldest issue = 1 (derived, ascending rank by date).
    pub issue_no: i64,
    pub preheader: String,
    /// Distinct sources with a known bias (l + c + r).
    pub source_count: i64,
    pub bias_l: i64,
    pub bias_c: i64,
    pub bias_r: i64,
    pub must: i64,
    pub should: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Page {
    pub issues: Vec<IssueRow>,
    pub has_more: bool,
    /// Cursor for the next page (the oldest date on this page) when `has_more`.
    pub next_before: Option<String>,
}

/// source_id -> bias bucket ('l' | 'c' | 'r'). Unknown/absent ids are simply not in the map.
fn bias_map() -> HashMap<String, char> {
    #[derive(Deserialize)]
    struct RawSource {
        id: String,
        bias: String,
    }
    let raw: Vec<RawSource> =
        serde_json::from_str(include_str!("../sources.json")).unwrap_or_default();
    raw.into_iter()
        .filter_map(|s| {
            let bucket = match s.bias.as_str() {
                "far-left" | "left" | "lean-left" => 'l',
                "center" => 'c',
                "lean-right" | "right" | "far-right" => 'r',
                _ => return None,
            };
            Some((s.id, bucket))
        })
        .collect()
}

/// A page of past issues, newest first, older than `before` (exclusive) if given.
pub fn fetch_archive(
    db_path: &str,
    before: Option<&str>,
    limit: i64,
) -> Result<Page, (StatusCode, String)> {
    let limit = limit.clamp(1, MAX_LIMIT);
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Base rows: fetch limit+1 so we can tell whether there's another page.
    struct Base {
        date: String,
        preheader: String,
        run_id: Option<i64>,
        issue_no: i64,
        must: i64,
        should: i64,
    }
    let mut bases: Vec<Base> = {
        let mut stmt = conn
            .prepare(
                "SELECT d.date, COALESCE(d.preheader,'') AS preheader, d.run_id,
                        (SELECT COUNT(*) FROM digests d2 WHERE d2.date <= d.date) AS issue_no,
                        (SELECT COUNT(DISTINCT sn.headline) FROM shown_narratives sn
                           WHERE sn.run_id = d.run_id AND sn.tier = 'must_know')   AS must,
                        (SELECT COUNT(DISTINCT sn.headline) FROM shown_narratives sn
                           WHERE sn.run_id = d.run_id AND sn.tier = 'should_know') AS should
                 FROM digests d
                 WHERE (?1 IS NULL OR d.date < ?1)
                 ORDER BY d.date DESC
                 LIMIT ?2",
            )
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?;
        stmt.query_map(rusqlite::params![before, limit + 1], |row| {
            Ok(Base {
                date: row.get(0)?,
                preheader: row.get(1)?,
                run_id: row.get(2)?,
                issue_no: row.get(3)?,
                must: row.get(4)?,
                should: row.get(5)?,
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "digests"))
        .collect()
    };

    let has_more = bases.len() as i64 > limit;
    bases.truncate(limit as usize);

    // Per-run bias split: one grouped query over the page's runs, bucketed in Rust via sources.json.
    let run_ids: Vec<i64> = bases.iter().filter_map(|b| b.run_id).collect();
    let mut split: HashMap<i64, (i64, i64, i64)> = HashMap::new(); // run_id -> (l, c, r)
    if !run_ids.is_empty() {
        let bmap = bias_map();
        let placeholders = vec!["?"; run_ids.len()].join(",");
        let sql = format!(
            "SELECT run_id, source_id FROM shown_narratives \
             WHERE run_id IN ({placeholders}) GROUP BY run_id, source_id"
        );
        let mut stmt = conn.prepare(&sql).map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?;
        let rows = stmt
            .query_map(rusqlite::params_from_iter(run_ids.iter()), |row| {
                let run_id: i64 = row.get(0)?;
                let source_id: Option<String> = row.get(1)?;
                Ok((run_id, source_id))
            })
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?
            .filter_map(|r| log_row_error(r, "shown_narratives"));
        for (run_id, source_id) in rows {
            // a null/unknown source_id has no known bias -> not counted (never panics)
            let Some(bucket) = source_id.as_deref().and_then(|id| bmap.get(id)).copied() else {
                continue;
            };
            let e = split.entry(run_id).or_insert((0, 0, 0));
            match bucket {
                'l' => e.0 += 1,
                'c' => e.1 += 1,
                'r' => e.2 += 1,
                _ => {}
            }
        }
    }

    let issues: Vec<IssueRow> = bases
        .into_iter()
        .map(|b| {
            let (l, c, r) = b
                .run_id
                .and_then(|id| split.get(&id).copied())
                .unwrap_or((0, 0, 0));
            IssueRow {
                date: b.date,
                issue_no: b.issue_no,
                preheader: b.preheader,
                source_count: l + c + r,
                bias_l: l,
                bias_c: c,
                bias_r: r,
                must: b.must,
                should: b.should,
            }
        })
        .collect();

    let next_before = if has_more {
        issues.last().map(|i| i.date.clone())
    } else {
        None
    };
    Ok(Page {
        issues,
        has_more,
        next_before,
    })
}

#[derive(Deserialize, Default)]
pub struct ArchiveQuery {
    pub before: Option<String>,
    pub limit: Option<i64>,
}

/// "2026-07-03" -> "3 Jul" (day + abbreviated month).
fn fmt_day_mon(date: &str) -> String {
    const M: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let p: Vec<&str> = date.split('-').collect();
    if p.len() != 3 {
        return date.to_string();
    }
    match (p[1].parse::<usize>(), p[2].parse::<i64>()) {
        (Ok(mo), Ok(d)) if (1..=12).contains(&mo) => format!("{d} {}", M[mo - 1]),
        _ => date.to_string(),
    }
}

/// Render one issue row `<li>`, matching the index list markup. `is_today` highlights the latest issue.
/// The bias bar's `aria-label` carries the L/C/R split as text (WCAG 1.4.1 — not colour-only).
pub fn row_html(row: &IssueRow, is_today: bool) -> String {
    let total = row.bias_l + row.bias_c + row.bias_r;
    let bias = if total > 0 {
        let lp = (row.bias_l as f64 / total as f64 * 100.0).round() as i64;
        let cp = (row.bias_c as f64 / total as f64 * 100.0).round() as i64;
        let rp = 100 - lp - cp; // absorb rounding drift into the last segment
        format!(
            r#"<span class="bias" role="img" aria-label="{c} sources: {l} left, {ct} center, {r} right"><i class="l" style="width:{lp}%"></i><i class="c" style="width:{cp}%"></i><i class="r" style="width:{rp}%"></i></span>"#,
            c = row.source_count,
            l = row.bias_l,
            ct = row.bias_c,
            r = row.bias_r
        )
    } else {
        String::new()
    };
    let today = if is_today { " today" } else { "" };
    let year = row.date.get(0..4).unwrap_or("");
    format!(
        concat!(
            r#"<li class="issue{today}" data-year="{year}"><a href="/{date}">"#,
            r#"<span class="idx"><span class="no">{no}</span><span class="date">{dm}</span></span>"#,
            r#"<span class="main"><span class="sumline">{pre}</span></span>"#,
            r#"<span class="rt">{bias}<span class="count">{c} sources</span></span></a></li>"#
        ),
        today = today,
        year = year,
        date = row.date,
        no = row.issue_no,
        dm = fmt_day_mon(&row.date),
        pre = escape_html(&row.preheader),
        bias = bias,
        c = row.source_count
    )
}

/// `GET /archive?before=&limit=` — HTML `<li>` fragment for the index's load-more (HTML-over-the-wire).
pub async fn archive_fragment(
    State(state): State<Arc<AppState>>,
    Query(q): Query<ArchiveQuery>,
) -> Result<Html<String>, (StatusCode, String)> {
    let page = fetch_archive(
        &state.db_path,
        q.before.as_deref(),
        q.limit.unwrap_or(DEFAULT_LIMIT),
    )?;
    let mut out = String::with_capacity(page.issues.len() * 400);
    for row in &page.issues {
        out.push_str(&row_html(row, false)); // appended rows are never "today"
    }
    Ok(Html(out))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static COUNTER: AtomicU32 = AtomicU32::new(0);

    /// Throwaway on-disk sqlite seeded with digests + shown_narratives (mirrors handlers' fixture).
    fn seed_db(
        digests: &[(&str, Option<i64>, &str)], // (date, run_id, preheader)
        narratives: &[(i64, &str, &str, Option<&str>)], // (run_id, headline, tier, source_id)
    ) -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir()
            .join(format!("archive_test_{}_{n}.db", std::process::id()))
            .to_string_lossy()
            .into_owned();
        let _ = std::fs::remove_file(&path);
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE digests (date TEXT PRIMARY KEY, preheader TEXT, run_id INTEGER);
             CREATE TABLE shown_narratives (run_id INTEGER, headline TEXT, tier TEXT, source_id TEXT);",
        )
        .unwrap();
        for (date, run_id, preheader) in digests {
            conn.execute(
                "INSERT INTO digests (date, run_id, preheader) VALUES (?1, ?2, ?3)",
                rusqlite::params![date, run_id, preheader],
            )
            .unwrap();
        }
        for (run_id, headline, tier, source_id) in narratives {
            conn.execute(
                "INSERT INTO shown_narratives (run_id, headline, tier, source_id) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![run_id, headline, tier, source_id],
            )
            .unwrap();
        }
        drop(conn);
        path
    }

    #[test]
    fn bias_map_buckets_known_ids() {
        let m = bias_map();
        assert_eq!(m.get("al_jazeera"), Some(&'l')); // lean-left
        assert_eq!(m.get("bbc_world"), Some(&'c')); // center
        assert_eq!(m.get("globe_and_mail"), Some(&'r')); // lean-right
    }

    #[test]
    fn computes_issue_number_counts_and_bias_split() {
        let path = seed_db(
            &[
                ("2026-01-01", Some(1), "oldest"),
                ("2026-01-02", Some(2), "newest"),
            ],
            &[
                // run 2: two must-know headlines, one should-know; 3 distinct sources l/c/r
                (2, "A", "must_know", Some("al_jazeera")), // l
                (2, "A", "must_know", Some("bbc_world")),  // c  (same headline, 2nd source)
                (2, "B", "must_know", Some("globe_and_mail")), // r
                (2, "C", "should_know", Some("bbc_world")), // c
            ],
        );
        let page = fetch_archive(&path, None, 30).unwrap();
        std::fs::remove_file(&path).ok();

        assert_eq!(page.issues.len(), 2);
        // newest first
        let newest = &page.issues[0];
        assert_eq!(newest.date, "2026-01-02");
        assert_eq!(newest.issue_no, 2); // ascending rank: oldest=1, newest=2
        assert_eq!(newest.must, 2); // distinct headlines A, B
        assert_eq!(newest.should, 1); // C
        // bias counts DISTINCT sources: al_jazeera(l), bbc_world(c, once despite 2 stories), globe(r)
        assert_eq!((newest.bias_l, newest.bias_c, newest.bias_r), (1, 1, 1));
        assert_eq!(newest.source_count, 3);
        // oldest has issue_no 1 and no narratives
        assert_eq!(page.issues[1].issue_no, 1);
        assert_eq!(page.issues[1].source_count, 0);
        assert!(!page.has_more);
        assert_eq!(page.next_before, None);
    }

    #[test]
    fn cursor_paginates_and_sets_has_more() {
        let path = seed_db(
            &[
                ("2026-01-01", Some(1), ""),
                ("2026-01-02", Some(2), ""),
                ("2026-01-03", Some(3), ""),
            ],
            &[],
        );
        // limit 2 -> newest two, has_more true, cursor at the oldest shown
        let page = fetch_archive(&path, None, 2).unwrap();
        assert_eq!(page.issues.len(), 2);
        assert_eq!(page.issues[0].date, "2026-01-03");
        assert_eq!(page.issues[1].date, "2026-01-02");
        assert!(page.has_more);
        assert_eq!(page.next_before.as_deref(), Some("2026-01-02"));
        // next page: before the cursor
        let page2 = fetch_archive(&path, Some("2026-01-02"), 2).unwrap();
        std::fs::remove_file(&path).ok();
        assert_eq!(page2.issues.len(), 1);
        assert_eq!(page2.issues[0].date, "2026-01-01");
        assert!(!page2.has_more);
    }

    #[test]
    fn null_run_id_and_null_source_id_do_not_panic() {
        let path = seed_db(
            &[("2026-01-01", None, "legacy no run_id")],
            &[(1, "orphan", "must_know", None)], // null source_id, unrelated run
        );
        let page = fetch_archive(&path, None, 30).unwrap();
        std::fs::remove_file(&path).ok();
        assert_eq!(page.issues.len(), 1);
        assert_eq!(page.issues[0].issue_no, 1);
        assert_eq!(page.issues[0].source_count, 0);
        assert_eq!(
            (
                page.issues[0].bias_l,
                page.issues[0].bias_c,
                page.issues[0].bias_r
            ),
            (0, 0, 0)
        );
    }

    #[test]
    fn row_html_renders_markup_escapes_and_aria_split() {
        let row = IssueRow {
            date: "2026-07-03".into(),
            issue_no: 200,
            preheader: "Kyiv & <b>Iran</b>".into(),
            source_count: 24,
            bias_l: 10,
            bias_c: 12,
            bias_r: 2,
            must: 5,
            should: 12,
        };
        let h = row_html(&row, true);
        assert!(h.contains(r#"href="/2026-07-03""#));
        assert!(h.contains(r#"<span class="date">3 Jul</span>"#));
        assert!(h.contains(r#"<span class="no">200</span>"#));
        assert!(h.contains("24 sources"));
        assert!(h.contains(r#"class="issue today""#));
        assert!(h.contains(r#"data-year="2026""#));
        // a11y: the L/C/R split is announced as text, not colour-only (WCAG 1.4.1)
        assert!(h.contains(r#"aria-label="24 sources: 10 left, 12 center, 2 right""#));
        // preheader is HTML-escaped
        assert!(h.contains("Kyiv &amp; &lt;b&gt;Iran&lt;/b&gt;"));
        // widths absorb rounding into the last segment (sum to 100)
        assert!(
            h.contains(r#"width:42%"#) && h.contains(r#"width:50%"#) && h.contains(r#"width:8%"#)
        );
    }
}
