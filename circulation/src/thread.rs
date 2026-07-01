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
use crate::templates::{render_thread, render_threads_index};
use crate::util::log_row_error;

/// One day's installment in a thread's history.
pub struct ThreadEntry {
    pub day: String, // YYYY-MM-DD, always present (from digest_runs.run_at)
    pub digest_date: Option<String>, // Some(date) when that day's digest exists (link target)
    pub cluster_story: String, // that day's matched story label
    pub delta: String, // synthesized "what's new" prose for the day; "" if none
}

pub struct ThreadDetail {
    pub label: String,
    pub status: String,
    pub entries: Vec<ThreadEntry>,
}

pub struct ThreadSummary {
    pub id: i64,
    pub label: String,
    pub status: String,
    pub updated_at: String,
}

/// Fetch one thread's header + full history, newest-first. `Ok(None)` means no such thread.
pub fn fetch_thread(
    db_path: &str,
    thread_id: i64,
) -> Result<Option<ThreadDetail>, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    let head: Option<(String, String)> = conn
        .query_row(
            "SELECT label, status FROM threads WHERE id = ?1",
            [thread_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?;

    let Some((label, status)) = head else {
        return Ok(None);
    };

    // digest_date via digests.run_id (the actual FK), not a date-string join -- avoids any
    // timezone/format drift between digest_runs.run_at and digests.date.
    let mut stmt = conn
        .prepare(
            "SELECT date(dr.run_at), d.date, COALESCE(ti.cluster_story, ''), ti.content
             FROM thread_installments ti
             JOIN digest_runs dr ON dr.id = ti.run_id
             LEFT JOIN digests d ON d.run_id = ti.run_id
             WHERE ti.thread_id = ?1
             ORDER BY ti.run_id DESC",
        )
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?;

    let entries: Vec<ThreadEntry> = stmt
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
            cluster_story,
            delta: delta_from_content(content.as_deref()),
        })
        .collect();

    Ok(Some(ThreadDetail {
        label,
        status,
        entries,
    }))
}

/// Fetch all threads for the `/threads` index, active first, most-recently-updated first within
/// each group.
pub fn fetch_thread_summaries(db_path: &str) -> Result<Vec<ThreadSummary>, (StatusCode, String)> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("DB error: {e}")))?;

    let mut stmt = conn
        .prepare(
            "SELECT id, label, status, updated_at FROM threads
             ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC",
        )
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Query error: {e}"),
            )
        })?;

    let summaries = stmt
        .query_map([], |row| {
            Ok(ThreadSummary {
                id: row.get(0)?,
                label: row.get(1)?,
                status: row.get(2)?,
                updated_at: row.get(3)?,
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
fn delta_from_content(content: Option<&str>) -> String {
    let Some(content) = content else {
        return String::new();
    };
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(content) else {
        return String::new();
    };
    let Some(whats_new) = parsed.get("whats_new").and_then(|v| v.as_array()) else {
        return String::new();
    };
    whats_new
        .iter()
        .take(3)
        .filter_map(|f| f.get("fact").and_then(|v| v.as_str()))
        .filter(|s| !s.is_empty())
        .map(strip_article_ids)
        .collect::<Vec<_>>()
        .join(" ")
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

    Ok(Html(render_thread(&state.digest_name, &detail)))
}

/// Thread index -- `GET /threads`, active threads first.
pub async fn threads_index(
    State(state): State<Arc<AppState>>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let summaries = fetch_thread_summaries(&state.db_path).map_err(|(_, e)| {
        tracing::error!("thread index fetch failed: {e}");
        (StatusCode::SERVICE_UNAVAILABLE, "Threads unavailable")
    })?;

    Ok(Html(render_threads_index(&state.digest_name, &summaries)))
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

    mod delta_from_content_tests {
        use super::super::delta_from_content;

        #[test]
        fn missing_content_is_empty() {
            assert_eq!(delta_from_content(None), "");
        }

        #[test]
        fn corrupt_json_is_empty() {
            assert_eq!(delta_from_content(Some("not json")), "");
        }

        #[test]
        fn joins_top_three_facts_and_strips_ids() {
            let content = r#"{"whats_new": [
                {"fact": "First fact [A1]", "sources": ["A1"]},
                {"fact": "Second fact [A2, A3]", "sources": ["A2", "A3"]},
                {"fact": "Third fact", "sources": []},
                {"fact": "Fourth fact (dropped)", "sources": []}
            ]}"#;
            assert_eq!(
                delta_from_content(Some(content)),
                "First fact Second fact Third fact"
            );
        }

        #[test]
        fn empty_whats_new_is_empty() {
            assert_eq!(delta_from_content(Some(r#"{"whats_new": []}"#)), "");
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
        assert_eq!(detail.entries[0].cluster_story, "Talks continue");
        assert_eq!(detail.entries[0].delta, "Deal signed");

        assert_eq!(detail.entries[1].day, "2026-06-28");
        assert_eq!(
            detail.entries[1].digest_date,
            Some("2026-06-28".to_string())
        );
        assert_eq!(detail.entries[1].cluster_story, "Talks begin");
        assert_eq!(detail.entries[1].delta, "");
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
        assert!(html.contains("/2026-06-28")); // links to the day's digest
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
            http_client: reqwest::Client::new(),
        }
    }
}
