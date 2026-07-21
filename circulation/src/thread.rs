//! Thread history page -- public read-only view of an evolving story-thread's per-day record.
//! See `newsroom/src/threads.py` for the pipeline side that writes these rows.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::Html,
};
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use std::sync::Arc;

use crate::AppState;
use crate::handlers::{brand_html, sub_chrome};
use crate::routes;
use crate::templates::{ThreadParams, ThreadsIndexParams, render_thread, render_threads_index};
use crate::util::log_row_error;

/// One day's installment in a thread's history.
pub struct ThreadEntry {
    pub day: String, // YYYY-MM-DD, always present (from digest_runs.run_at)
    pub digest_date: Option<String>, // Some(date) when that day's digest exists (link target)
    pub headline: String, // that day's matched story label (cluster_story)
    pub facts: Vec<String>, // top-3 "what's new" facts, article-ids stripped; empty if none
}

pub struct ThreadDetail {
    pub label: String,
    pub status: String,
    pub entries: Vec<ThreadEntry>,
    /// Open "still watching" questions (from `thread_questions`), for the ledger.
    pub open_questions: Vec<String>,
}

pub struct ThreadSummary {
    pub id: i64,
    pub label: String,
    pub status: String,
    pub updated_at: String,
    /// Number of installments recorded for the thread.
    pub update_count: i64,
    /// The latest installment's story headline (the running-order "what's the state now" line).
    pub summary: String,
}

/// True when the error is SQLite's "no such table" -- the shape a stale DB clone takes (e.g.
/// `digest-cloud.db` predates the thread_* tables). Callers degrade to an empty state instead of
/// 503-ing, so `make circulation` against a partial clone renders an empty threads page.
fn is_missing_table(e: &rusqlite::Error) -> bool {
    e.to_string().contains("no such table")
}

/// Prepare `sql`, mapping any real failure to a 500. SQLite's "no such table" instead yields
/// `Ok(None)` so the caller can degrade a stale/partial DB clone to an empty result (see
/// `is_missing_table`) rather than 503-ing.
fn prepare_or_degrade<'c>(
    conn: &'c Connection,
    sql: &str,
) -> Result<Option<rusqlite::Statement<'c>>, (StatusCode, String)> {
    match conn.prepare(sql) {
        Ok(stmt) => Ok(Some(stmt)),
        // Log rather than swallow silently: `{e}` names the actual missing table, so a stale
        // clone reads as "no such table: thread_*" while an unexpectedly-absent core table
        // (e.g. a half-applied migration) still leaves a visible signal instead of a blank page.
        Err(e) if is_missing_table(&e) => {
            tracing::warn!("degrading to empty result -- {e} (stale/partial DB clone?)");
            Ok(None)
        }
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Query error: {e}"),
        )),
    }
}

/// Fetch one thread's header + full history, newest-first. `Ok(None)` means no such thread.
pub fn fetch_thread(
    db_path: &str,
    thread_id: i64,
) -> Result<Option<ThreadDetail>, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    let head: Option<(String, String)> = match conn
        .query_row(
            "SELECT label, status FROM threads WHERE id = ?1",
            [thread_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
    {
        Ok(head) => head,
        // Stale clone without the thread tables: treat as "no such thread" rather than 503.
        Err(e) if is_missing_table(&e) => {
            tracing::warn!(
                "thread {thread_id}: treating as unknown -- {e} (stale/partial DB clone?)"
            );
            return Ok(None);
        }
        Err(e) => {
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            ));
        }
    };

    let Some((label, status)) = head else {
        return Ok(None);
    };

    // digest_date via digests.run_id (the actual FK), not a date-string join -- avoids any
    // timezone/format drift between digest_runs.run_at and digests.date.
    // Partial clone missing the installments table: header with no history, not a 503.
    let entries: Vec<ThreadEntry> = match prepare_or_degrade(
        &conn,
        "SELECT date(dr.run_at), d.date, COALESCE(ti.cluster_story, ''), ti.content
             FROM thread_installments ti
             JOIN digest_runs dr ON dr.id = ti.run_id
             LEFT JOIN digests d ON d.run_id = ti.run_id
             WHERE ti.thread_id = ?1
             ORDER BY ti.run_id DESC",
    )? {
        Some(mut stmt) => stmt
            .query_map([thread_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            })
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("Query error: {e}"),
                )
            })?
            .filter_map(|r| log_row_error(r, "thread_installments"))
            .map(|(day, digest_date, cluster_story, content)| ThreadEntry {
                day,
                digest_date,
                headline: cluster_story,
                facts: facts_from_content(content.as_deref()),
            })
            .collect(),
        None => Vec::new(),
    };

    let open_questions = fetch_open_questions(&conn, thread_id)?;
    Ok(Some(ThreadDetail {
        label,
        status,
        entries,
        open_questions,
    }))
}

/// The thread's most-recently-raised open questions — the current "still watching" frontier, newest
/// first. Reads `thread_questions` (previously unqueried). Capped: the newsroom pipeline raises many
/// questions per installment and rarely marks them resolved, so "open" accumulates (some threads carry
/// 40+); the ledger surfaces the recent frontier, not the full backlog. A proper resolve/dedup pass in
/// the pipeline is the real fix (going-forward newsroom work).
const LEDGER_MAX: usize = 6;

fn fetch_open_questions(
    conn: &Connection,
    thread_id: i64,
) -> Result<Vec<String>, (StatusCode, String)> {
    // Partial clone without the questions table: no ledger, not a 503.
    let Some(mut stmt) = prepare_or_degrade(
        conn,
        "SELECT question FROM thread_questions
             WHERE thread_id = ?1 AND status = 'open'
             ORDER BY raised_run_id DESC, id DESC LIMIT ?2",
    )?
    else {
        return Ok(Vec::new());
    };
    let qs = stmt
        .query_map(rusqlite::params![thread_id, LEDGER_MAX as i64], |row| {
            row.get::<_, String>(0)
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "thread_questions"))
        .collect();
    Ok(qs)
}

/// Fetch all threads for the `/threads` index, active first, most-recently-updated first within
/// each group.
pub fn fetch_thread_summaries(db_path: &str) -> Result<Vec<ThreadSummary>, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    // Stale clone without the thread tables: render an empty threads index, not a 503.
    let Some(mut stmt) = prepare_or_degrade(
        &conn,
        "SELECT t.id, t.label, t.status, t.updated_at,
                    (SELECT COUNT(*) FROM thread_installments ti WHERE ti.thread_id = t.id) AS update_count,
                    (SELECT ti.content FROM thread_installments ti
                     WHERE ti.thread_id = t.id ORDER BY ti.run_id DESC LIMIT 1) AS latest_content
             FROM threads t
             ORDER BY CASE WHEN t.status = 'active' THEN 0 ELSE 1 END, t.updated_at DESC",
    )?
    else {
        return Ok(Vec::new());
    };

    let summaries = stmt
        .query_map([], |row| {
            let latest_content: Option<String> = row.get(5)?;
            Ok(ThreadSummary {
                id: row.get(0)?,
                label: row.get(1)?,
                status: row.get(2)?,
                updated_at: row.get(3)?,
                update_count: row.get(4)?,
                // The latest development, not the headline — the label is usually the same as the
                // latest cluster_story, so the top whats_new fact gives a distinct "what's new" line.
                summary: facts_from_content(latest_content.as_deref())
                    .into_iter()
                    .next()
                    .unwrap_or_default(),
            })
        })
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?
        .filter_map(|r| log_row_error(r, "threads"))
        .collect();

    Ok(summaries)
}

/// Parse a stored installment's `content` JSON and render its "what's new" delta: the top 3
/// `whats_new` facts joined as prose. Mirrors `threads.delta_from_facts` (`newsroom/src/threads.py`)
/// -- same top_n, same leak-guard. Returns "" on missing/corrupt content, mirroring the Python
/// side's fail-open behaviour (a rendering hiccup must never break the page).
fn facts_from_content(content: Option<&str>) -> Vec<String> {
    let Some(content) = content else {
        return Vec::new();
    };
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(content) else {
        return Vec::new();
    };
    let Some(whats_new) = parsed.get("whats_new").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    whats_new
        .iter()
        .take(3)
        .filter_map(|f| f.get("fact").and_then(|v| v.as_str()))
        .filter(|s| !s.is_empty())
        .map(strip_article_ids)
        .collect()
}

/// Mirror of Python's `threads.strip_article_ids` (`newsroom/src/threads.py`) -- keep in sync.
/// Removes leaked internal `[A123]` / `[A1, A2]` article-id citations from synthesized fact
/// prose before it reaches this public page: those ids are internal audit provenance and must
/// never reach readers (see commit `1b804a7`, the id-leak bug this guards against; the thread
/// path bypasses COHERENCE's leak guard so already-stored rows may still carry them).
fn strip_article_ids(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < text.len() {
        let ws_len = text[i..].len() - text[i..].trim_start().len();
        if let Some(citation_len) = match_citation(&text[i + ws_len..]) {
            i += ws_len + citation_len;
            continue;
        }
        let ch = text[i..].chars().next().expect("i < text.len()");
        out.push(ch);
        i += ch.len_utf8();
    }
    collapse_whitespace_runs(&out)
}

/// Matches `\[A\d+(?:\s*,\s*A\d+)*\]` at the start of `s`; returns the byte length consumed.
fn match_citation(s: &str) -> Option<usize> {
    let mut rest = s.strip_prefix('[')?;
    rest = match_article_id(rest)?;
    while let Some(candidate) = rest.trim_start().strip_prefix(',').map(str::trim_start) {
        match match_article_id(candidate) {
            Some(next) => rest = next,
            None => break,
        }
    }
    let rest = rest.strip_prefix(']')?;
    Some(s.len() - rest.len())
}

/// Matches `A\d+` at the start of `s`; returns the remainder after it.
///
/// Digit predicate accepts ASCII digits OR `char::is_numeric()`, not just `is_ascii_digit()`:
/// Python's `\d` matches any Unicode `Nd` digit (e.g. fullwidth U+FF11-FF19), a strict superset
/// of ASCII 0-9, and `is_numeric()` is the closest std equivalent. This is a leak guard --
/// under-stripping lets internal `[A123]`-style provenance markers reach readers, so it must
/// never be narrower than the Python reference it mirrors; over-stripping is the safe direction.
fn match_article_id(s: &str) -> Option<&str> {
    let rest = s.strip_prefix('A')?;
    let digits = rest.len()
        - rest
            .trim_start_matches(|c: char| c.is_ascii_digit() || c.is_numeric())
            .len();
    if digits == 0 {
        return None;
    }
    Some(&rest[digits..])
}

/// Collapse runs of 2+ whitespace chars into one space, then trim ends -- mirrors Python's
/// `re.sub(r"\s{2,}", " ", text).strip()`.
fn collapse_whitespace_runs(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c.is_whitespace() {
            let mut run = 1;
            while chars.peek().is_some_and(|c2| c2.is_whitespace()) {
                chars.next();
                run += 1;
            }
            out.push(if run >= 2 { ' ' } else { c });
        } else {
            out.push(c);
        }
    }
    out.trim().to_string()
}

/// Thread history page -- `GET /thread/{id}`. 404s on both a non-numeric id and an unknown id
/// (no distinction reader-visible; both mean "nothing here").
pub async fn thread_page(
    Path(id): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let thread_id: i64 = id
        .parse()
        .map_err(|_| (StatusCode::NOT_FOUND, "Thread not found"))?;

    let detail = fetch_thread(&state.db_path, thread_id)
        .map_err(|(_, e)| {
            tracing::error!("thread fetch failed: {e}");
            (StatusCode::SERVICE_UNAVAILABLE, "Thread unavailable")
        })?
        .ok_or((StatusCode::NOT_FOUND, "Thread not found"))?;

    // Detail pages keep every section in the nav (no current-section omission).
    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "",
        &format!("{}/{thread_id}", routes::THREAD),
        "An automated daily briefing. Curated by Claude, filed by a human. &copy; Sean Floyd",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    Ok(Html(render_thread(&ThreadParams {
        brand_html: &brand,
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        detail: &detail,
    })))
}

/// Thread index -- `GET /threads`, active threads first.
pub async fn threads_index(
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let summaries = fetch_thread_summaries(&state.db_path).map_err(|(_, e)| {
        tracing::error!("thread index fetch failed: {e}");
        (StatusCode::SERVICE_UNAVAILABLE, "Threads unavailable")
    })?;

    let (topbar_html, footer_html) = sub_chrome(
        &state,
        "threads",
        routes::THREADS,
        "A thread groups a running story's daily updates. Ongoing threads carry today's digest forward.",
    );
    let brand = brand_html(&state.digest_name);
    let canonical_url = state.base_url();
    let image_url = state.og_image_url();
    Ok(Html(render_threads_index(&ThreadsIndexParams {
        title: &state.digest_name,
        brand_html: &brand,
        home_url: "/",
        canonical_url: &canonical_url,
        feed_url: routes::FEED,
        image_url: &image_url,
        font_url: &state.font_url,
        topbar_html: &topbar_html,
        footer_html: &footer_html,
        threads: &summaries,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    // --- strip_article_ids: mirrors newsroom/tests/test_threads.py exactly (parity check) ---

    mod strip_article_ids_tests {
        use super::super::strip_article_ids;

        #[test]
        fn removes_trailing_multi_id_citation() {
            assert_eq!(
                strip_article_ids("Talks resumed in Doha. [A221, A407]"),
                "Talks resumed in Doha."
            );
        }

        #[test]
        fn removes_midsentence_citation_without_double_space() {
            assert_eq!(
                strip_article_ids("Iran denied it [A164, A407, A429] and stalled."),
                "Iran denied it and stalled."
            );
        }

        #[test]
        fn removes_single_id() {
            assert_eq!(
                strip_article_ids("Quake hit northern Venezuela [A119]"),
                "Quake hit northern Venezuela"
            );
        }

        #[test]
        fn handles_no_space_list() {
            assert_eq!(strip_article_ids("x [A1,A2,A3] y"), "x y");
        }

        #[test]
        fn removes_leading_citation() {
            assert_eq!(strip_article_ids("[A1] Big news today"), "Big news today");
        }

        #[test]
        fn collapses_multiple_separated_citations() {
            assert_eq!(strip_article_ids("a [A1] b [A2] c"), "a b c");
        }

        #[test]
        fn preserves_numeric_source_markers() {
            assert_eq!(
                strip_article_ids("See report [1] and [2]"),
                "See report [1] and [2]"
            );
        }

        #[test]
        fn preserves_non_id_brackets() {
            assert_eq!(
                strip_article_ids("a note [draft] here"),
                "a note [draft] here"
            );
        }

        #[test]
        fn passthrough_clean_text() {
            assert_eq!(
                strip_article_ids("Nothing to strip here."),
                "Nothing to strip here."
            );
        }

        #[test]
        fn empty_string() {
            assert_eq!(strip_article_ids(""), "");
        }

        #[test]
        fn strips_fullwidth_unicode_digits() {
            // Python's \d matches any Unicode Nd digit (e.g. fullwidth U+FF11-FF19), not just
            // ASCII 0-9 -- the leak guard must strip at least as aggressively as Python does.
            assert_eq!(
                strip_article_ids("Talks resumed [A\u{FF11}\u{FF12}\u{FF13}] today"),
                "Talks resumed today"
            );
        }

        #[test]
        fn ascii_digit_behaviour_unchanged() {
            assert_eq!(
                strip_article_ids("Quake hit [A123] region"),
                "Quake hit region"
            );
        }

        #[test]
        fn leaves_non_digit_bracket_alone() {
            assert_eq!(strip_article_ids("a note [Ax] here"), "a note [Ax] here");
        }
    }

    mod facts_from_content_tests {
        use super::super::facts_from_content;

        #[test]
        fn missing_content_is_empty() {
            assert!(facts_from_content(None).is_empty());
        }

        #[test]
        fn corrupt_json_is_empty() {
            assert!(facts_from_content(Some("not json")).is_empty());
        }

        #[test]
        fn takes_top_three_facts_and_strips_ids() {
            let content = r#"{"whats_new": [
                {"fact": "First fact [A1]", "sources": ["A1"]},
                {"fact": "Second fact [A2, A3]", "sources": ["A2", "A3"]},
                {"fact": "Third fact", "sources": []},
                {"fact": "Fourth fact (dropped)", "sources": []}
            ]}"#;
            assert_eq!(
                facts_from_content(Some(content)),
                vec!["First fact", "Second fact", "Third fact"]
            );
        }

        #[test]
        fn empty_whats_new_is_empty() {
            assert!(facts_from_content(Some(r#"{"whats_new": []}"#)).is_empty());
        }
    }

    // --- handler tests: seeded temp-file SQLite DB (no existing precedent in this crate; this
    // establishes the pattern -- see task3c-report.md) ---

    static DB_COUNTER: AtomicU64 = AtomicU64::new(0);

    /// A temp SQLite file that deletes itself on drop, so failed tests don't litter tmp.
    struct TempDb {
        path: String,
    }

    impl TempDb {
        fn new() -> Self {
            let n = DB_COUNTER.fetch_add(1, Ordering::SeqCst);
            let path = std::env::temp_dir()
                .join(format!(
                    "circulation-thread-test-{}-{n}.db",
                    std::process::id()
                ))
                .to_string_lossy()
                .into_owned();
            let conn = Connection::open(&path).expect("open temp db");
            conn.execute_batch(
                "CREATE TABLE threads (
                    id INTEGER PRIMARY KEY,
                    slug TEXT,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    first_run_id INTEGER,
                    last_run_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE thread_installments (
                    id INTEGER PRIMARY KEY,
                    thread_id INTEGER NOT NULL,
                    run_id INTEGER NOT NULL,
                    cluster_story TEXT,
                    matched_score REAL,
                    content TEXT,
                    created_at TEXT
                );
                CREATE TABLE digest_runs (
                    id INTEGER PRIMARY KEY,
                    run_at TEXT
                );
                CREATE TABLE digests (
                    date TEXT PRIMARY KEY,
                    html TEXT,
                    run_id INTEGER
                );
                CREATE TABLE thread_questions (
                    id INTEGER PRIMARY KEY,
                    thread_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    raised_run_id INTEGER,
                    resolved_run_id INTEGER
                );",
            )
            .expect("create schema");
            Self { path }
        }

        fn seed(&self, sql: &str) {
            Connection::open(&self.path)
                .expect("open temp db")
                .execute_batch(sql)
                .expect("seed db");
        }
    }

    impl Drop for TempDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.path);
        }
    }

    #[test]
    fn fetch_thread_returns_none_for_unknown_id() {
        let db = TempDb::new();
        let result = fetch_thread(&db.path, 999).expect("query ok");
        assert!(result.is_none());
    }

    #[test]
    fn fetch_thread_orders_entries_newest_first_and_links_existing_digest() {
        let db = TempDb::new();
        db.seed(
            "INSERT INTO threads (id, label, status, updated_at) VALUES (1, 'Iran-US talks', 'active', '2026-06-30 10:00:00');
             INSERT INTO digest_runs (id, run_at) VALUES (10, '2026-06-28 06:00:00'), (11, '2026-06-29 06:00:00');
             INSERT INTO digests (date, html, run_id) VALUES ('2026-06-28', '<html></html>', 10);
             -- no digests row for run 11 -- still-in-progress / no digest that day
             INSERT INTO thread_installments (thread_id, run_id, cluster_story, content) VALUES
                (1, 10, 'Talks begin', NULL),
                (1, 11, 'Talks continue', '{\"whats_new\": [{\"fact\": \"Deal signed [A9]\", \"sources\": [\"A9\"]}]}');",
        );

        let detail = fetch_thread(&db.path, 1)
            .expect("query ok")
            .expect("thread exists");
        assert_eq!(detail.label, "Iran-US talks");
        assert_eq!(detail.status, "active");
        assert_eq!(detail.entries.len(), 2);

        // newest (run 11) first
        assert_eq!(detail.entries[0].day, "2026-06-29");
        assert_eq!(detail.entries[0].digest_date, None);
        assert_eq!(detail.entries[0].headline, "Talks continue");
        assert_eq!(detail.entries[0].facts, vec!["Deal signed"]);

        assert_eq!(detail.entries[1].day, "2026-06-28");
        assert_eq!(
            detail.entries[1].digest_date,
            Some("2026-06-28".to_string())
        );
        assert_eq!(detail.entries[1].headline, "Talks begin");
        assert!(detail.entries[1].facts.is_empty());
    }

    #[test]
    fn fetch_thread_reads_open_questions_for_the_ledger() {
        let db = TempDb::new();
        db.seed(
            "INSERT INTO threads (id, label, status, updated_at) VALUES (1, 'Q thread', 'active', '2026-06-30 10:00:00');
             INSERT INTO thread_questions (thread_id, question, status, raised_run_id) VALUES
                (1, 'Older open', 'open', 10),
                (1, 'Already answered', 'resolved', 11),
                (1, 'Newest open', 'open', 12);",
        );
        let detail = fetch_thread(&db.path, 1)
            .expect("query ok")
            .expect("exists");
        // ledger surfaces open questions, most-recently-raised first (resolved ones excluded)
        assert_eq!(detail.open_questions, vec!["Newest open", "Older open"]);
    }

    #[test]
    fn fetch_thread_summaries_lists_active_first() {
        let db = TempDb::new();
        db.seed(
            "INSERT INTO threads (id, label, status, updated_at) VALUES
                (1, 'Dormant story', 'dormant', '2026-06-29 08:00:00'),
                (2, 'Active story', 'active', '2026-06-25 08:00:00');",
        );

        let summaries = fetch_thread_summaries(&db.path).expect("query ok");
        assert_eq!(summaries.len(), 2);
        assert_eq!(summaries[0].label, "Active story");
        assert_eq!(summaries[1].label, "Dormant story");
    }

    #[tokio::test]
    async fn thread_page_renders_history_and_escapes() {
        let db = TempDb::new();
        db.seed(
            "INSERT INTO threads (id, label, status, updated_at) VALUES (1, '<b>Bold</b> story', 'active', '2026-06-30 10:00:00');
             INSERT INTO digest_runs (id, run_at) VALUES (10, '2026-06-28 06:00:00');
             INSERT INTO digests (date, html, run_id) VALUES ('2026-06-28', '<html></html>', 10);
             INSERT INTO thread_installments (thread_id, run_id, cluster_story, content) VALUES
                (1, 10, '<script>alert(1)</script>', NULL);",
        );
        let state = Arc::new(test_state(&db.path));

        let Ok(Html(html)) = thread_page(Path("1".to_string()), State(state)).await else {
            panic!("expected 200");
        };
        assert!(!html.contains("<script>alert(1)</script>"));
        assert!(html.contains("&lt;script&gt;alert(1)&lt;/script&gt;"));
        assert!(!html.contains("<b>Bold</b> story"));
        assert!(html.contains("&lt;b&gt;Bold&lt;/b&gt; story"));
        assert!(html.contains("/issues/2026-06-28")); // links to the day's digest
    }

    #[tokio::test]
    async fn thread_page_404s_on_unknown_id() {
        let db = TempDb::new();
        let state = Arc::new(test_state(&db.path));

        let err = thread_page(Path("999".to_string()), State(state))
            .await
            .expect_err("expected 404");
        assert_eq!(err.0, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn thread_page_404s_on_non_numeric_id() {
        let db = TempDb::new();
        let state = Arc::new(test_state(&db.path));

        let err = thread_page(Path("not-a-number".to_string()), State(state))
            .await
            .expect_err("expected 404");
        assert_eq!(err.0, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn threads_index_lists_all_threads() {
        let db = TempDb::new();
        db.seed(
            "INSERT INTO threads (id, label, status, updated_at) VALUES
                (1, 'First story', 'active', '2026-06-30 10:00:00'),
                (2, 'Second <i>story</i>', 'dormant', '2026-06-29 10:00:00');",
        );
        let state = Arc::new(test_state(&db.path));

        let Ok(Html(html)) = threads_index(State(state)).await else {
            panic!("expected 200");
        };
        assert!(html.contains("First story"));
        assert!(html.contains("Second &lt;i&gt;story&lt;/i&gt;"));
        assert!(!html.contains("Second <i>story</i>"));
        assert!(html.contains("/thread/1"));
        assert!(html.contains("/thread/2"));
    }

    fn test_state(db_path: &str) -> AppState {
        AppState {
            db_path: db_path.to_string(),
            digest_name: "Test Digest".to_string(),
            digest_domain: None,
            homepage_url: None,
            source_url: None,
            resend_api_key: None,
            resend_audience_id: None,
            feedback_email: None,
            font_url: "/assets/fonts/source-serif-4.test.woff2".to_string(),
            http_client: reqwest::Client::new(),
            subscribe_limiter: crate::handlers::RateLimiter::new(
                5,
                std::time::Duration::from_secs(3600),
            ),
            subscribe_token_secret: None,
            double_opt_in: false,
        }
    }
}
