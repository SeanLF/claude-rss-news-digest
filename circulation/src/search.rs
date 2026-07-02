use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Html,
};
use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;
use std::sync::Arc;

use crate::AppState;
use crate::templates::render_search;

/// Longest query we'll pass to FTS5 -- keeps the MATCH expression and the
/// rendered page bounded regardless of what a client sends.
const MAX_QUERY_LEN: usize = 200;

/// Max results returned -- an archive search, not a paginated index.
const MAX_RESULTS: usize = 50;

#[derive(Deserialize, Default)]
pub struct SearchQuery {
    pub q: Option<String>,
}

#[derive(Clone, Debug)]
pub struct SearchResult {
    pub headline: String,
    pub tier: String,
    /// Date of the digest this headline was shown in, if resolvable via the
    /// run_id -> digests join. None only for rows whose run never produced a
    /// digest row (e.g. a since-deleted run) -- shouldn't happen in practice.
    pub date: Option<String>,
}

/// Trim and length-cap a raw query string. Returns None for an empty/blank
/// query so callers can distinguish "no query yet" from "query, no results".
pub fn sanitize_query(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    // An embedded NUL reaches `fts_quote`'s string literal unescaped and
    // SQLite's tokenizer treats it as an unterminated string -- strip it
    // before capping length so `q=a%00b` degrades to a normal query instead
    // of a 503.
    Some(
        trimmed
            .chars()
            .filter(|c| *c != '\0')
            .take(MAX_QUERY_LEN)
            .collect(),
    )
}

/// Quote `q` as a single FTS5 string literal so query syntax (AND/OR/NOT,
/// `*`, `-`, parentheses, `column:` filters) has no special meaning -- the
/// entire input is matched as literal text, not parsed as an FTS5 query.
pub fn fts_quote(q: &str) -> String {
    format!("\"{}\"", q.replace('"', "\"\""))
}

/// Run the FTS5 search against an already-open connection. Kept separate
/// from the handler so it's testable against an in-memory database.
pub fn search_shown_narratives(conn: &Connection, q: &str) -> rusqlite::Result<Vec<SearchResult>> {
    let match_expr = fts_quote(q);
    let mut stmt = conn.prepare(
        "SELECT sn.headline, sn.tier, d.date
         FROM shown_narratives_fts f
         JOIN shown_narratives sn ON sn.id = f.rowid
         LEFT JOIN digests d ON d.run_id = sn.run_id
         WHERE shown_narratives_fts MATCH ?1
         ORDER BY f.rank
         LIMIT ?2",
    )?;
    stmt.query_map(rusqlite::params![match_expr, MAX_RESULTS as i64], |row| {
        Ok(SearchResult {
            headline: row.get(0)?,
            tier: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            date: row.get(2)?,
        })
    })?
    .collect()
}

/// GET /search?q= -- full-text search over shown headlines.
pub async fn search(
    State(state): State<Arc<AppState>>,
    Query(query): Query<SearchQuery>,
) -> Result<Html<String>, (StatusCode, &'static str)> {
    let sanitized = sanitize_query(&query.q.unwrap_or_default());

    let results = match &sanitized {
        Some(q) => {
            let conn =
                Connection::open_with_flags(&state.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
                    .map_err(|e| {
                        tracing::error!("failed to open db for search: {e}");
                        (StatusCode::SERVICE_UNAVAILABLE, "Search unavailable")
                    })?;
            search_shown_narratives(&conn, q).map_err(|e| {
                tracing::error!("search query failed: {e}");
                (StatusCode::SERVICE_UNAVAILABLE, "Search unavailable")
            })?
        }
        None => Vec::new(),
    };

    let html = render_search(&state.digest_name, sanitized.as_deref(), &results);
    Ok(Html(html))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build an in-memory DB with the same shape as the FTS migration
    /// (`migrations/20260701210002_shown_narratives_fts.sql`): shown_narratives,
    /// digest_runs, and digests, the external-content FTS5 table, and its
    /// insert/delete/update triggers.
    fn test_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "
            CREATE TABLE digest_runs (id INTEGER PRIMARY KEY);
            CREATE TABLE digests (date TEXT PRIMARY KEY, run_id INTEGER);
            CREATE TABLE shown_narratives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headline TEXT NOT NULL,
                tier TEXT,
                source_id TEXT,
                original_title TEXT,
                run_id INTEGER
            );
            CREATE VIRTUAL TABLE shown_narratives_fts USING fts5(
                headline,
                original_title,
                content='shown_narratives',
                content_rowid='id'
            );
            CREATE TRIGGER shown_narratives_fts_insert AFTER INSERT ON shown_narratives BEGIN
                INSERT INTO shown_narratives_fts(rowid, headline, original_title)
                VALUES (new.id, new.headline, new.original_title);
            END;
            CREATE TRIGGER shown_narratives_fts_delete AFTER DELETE ON shown_narratives BEGIN
                INSERT INTO shown_narratives_fts(shown_narratives_fts, rowid, headline, original_title)
                VALUES ('delete', old.id, old.headline, old.original_title);
            END;
            CREATE TRIGGER shown_narratives_fts_update AFTER UPDATE ON shown_narratives BEGIN
                INSERT INTO shown_narratives_fts(shown_narratives_fts, rowid, headline, original_title)
                VALUES ('delete', old.id, old.headline, old.original_title);
                INSERT INTO shown_narratives_fts(rowid, headline, original_title)
                VALUES (new.id, new.headline, new.original_title);
            END;
            ",
        )
        .unwrap();
        conn
    }

    fn seed(
        conn: &Connection,
        id: i64,
        headline: &str,
        tier: &str,
        original_title: &str,
        run_id: i64,
        date: &str,
    ) {
        conn.execute("INSERT INTO digest_runs (id) VALUES (?1)", [run_id])
            .ok(); // may already exist
        conn.execute(
            "INSERT OR IGNORE INTO digests (date, run_id) VALUES (?1, ?2)",
            rusqlite::params![date, run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO shown_narratives (id, headline, tier, original_title, run_id) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params![id, headline, tier, original_title, run_id],
        )
        .unwrap();
    }

    mod sanitize_query_tests {
        use super::*;

        #[test]
        fn trims_and_keeps_normal_query() {
            assert_eq!(
                sanitize_query("  fusion reactor  ").as_deref(),
                Some("fusion reactor")
            );
        }

        #[test]
        fn empty_or_blank_is_none() {
            assert_eq!(sanitize_query(""), None);
            assert_eq!(sanitize_query("   "), None);
        }

        #[test]
        fn caps_length() {
            let long = "a".repeat(500);
            let result = sanitize_query(&long).unwrap();
            assert_eq!(result.len(), MAX_QUERY_LEN);
        }

        /// A NUL byte reaches `fts_quote` -> the FTS5 MATCH string literal
        /// unescaped, which SQLite's tokenizer treats as an unterminated
        /// string, turning trivial input like `q=a%00b` into a 503 instead
        /// of a normal (possibly empty) result page.
        #[test]
        fn strips_embedded_nul() {
            assert_eq!(sanitize_query("a\0b").as_deref(), Some("ab"));
        }
    }

    mod fts_quote_tests {
        use super::*;

        #[test]
        fn wraps_in_quotes() {
            assert_eq!(fts_quote("fusion"), "\"fusion\"");
        }

        #[test]
        fn doubles_embedded_quotes() {
            assert_eq!(fts_quote("say \"hi\""), "\"say \"\"hi\"\"\"");
        }
    }

    mod search_shown_narratives_tests {
        use super::*;

        #[test]
        fn finds_seeded_row_by_keyword() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Fusion reactor breakthrough announced",
                "must_know",
                "Scientists hit fusion milestone",
                1,
                "2026-07-01",
            );

            let results = search_shown_narratives(&conn, "fusion").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].headline, "Fusion reactor breakthrough announced");
            assert_eq!(results[0].tier, "must_know");
            assert_eq!(results[0].date.as_deref(), Some("2026-07-01"));
        }

        #[test]
        fn finds_by_original_title_too() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Editorial headline",
                "should_know",
                "Tulip prices rise sharply",
                1,
                "2026-07-01",
            );

            let results = search_shown_narratives(&conn, "tulip").unwrap();
            assert_eq!(results.len(), 1);
        }

        #[test]
        fn no_results_for_unmatched_keyword() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Fusion reactor breakthrough",
                "must_know",
                "Fusion milestone",
                1,
                "2026-07-01",
            );

            let results = search_shown_narratives(&conn, "volcano").unwrap();
            assert!(results.is_empty());
        }

        #[test]
        fn deleted_row_disappears_from_search() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Fusion reactor breakthrough",
                "must_know",
                "Fusion milestone",
                1,
                "2026-07-01",
            );
            conn.execute("DELETE FROM shown_narratives WHERE id = 1", [])
                .unwrap();

            let results = search_shown_narratives(&conn, "fusion").unwrap();
            assert!(results.is_empty());
        }

        /// No code path UPDATEs shown_narratives today, but the trigger is
        /// insurance against a future one silently desyncing the index --
        /// prove it actually keeps the FTS row in step with an edit. Uses a
        /// term that's only ever in `headline` (not `original_title`, which
        /// the UPDATE leaves untouched) so a stale index can't coincidentally
        /// still match via the other indexed column.
        #[test]
        fn updated_row_is_reindexed() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Fusion reactor breakthrough",
                "must_know",
                "Unrelated original title",
                1,
                "2026-07-01",
            );
            conn.execute(
                "UPDATE shown_narratives SET headline = 'Volcano eruption update' WHERE id = 1",
                [],
            )
            .unwrap();

            assert!(
                search_shown_narratives(&conn, "breakthrough")
                    .unwrap()
                    .is_empty()
            );
            let results = search_shown_narratives(&conn, "volcano").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].headline, "Volcano eruption update");
        }

        #[test]
        fn injection_attempt_treated_as_literal_text_not_fts_syntax() {
            let conn = test_db();
            seed(
                &conn,
                1,
                "Ordinary headline about markets",
                "must_know",
                "Markets original",
                1,
                "2026-07-01",
            );

            // A raw NEAR/boolean-operator-laced or unbalanced-paren string would
            // be a syntax error if passed unquoted to MATCH. Quoted, it's just a
            // literal phrase that (correctly) matches nothing here.
            for attempt in [
                "\"; DROP TABLE shown_narratives; --",
                "head*(",
                "foo OR bar AND (",
                "col:value",
            ] {
                let result = search_shown_narratives(&conn, attempt);
                assert!(
                    result.is_ok(),
                    "expected literal-text match, got error for {attempt:?}: {result:?}"
                );
                assert!(result.unwrap().is_empty());
            }
        }

        #[test]
        fn results_ordered_and_limited() {
            let conn = test_db();
            for i in 1..=60 {
                seed(
                    &conn,
                    i,
                    &format!("Fusion story number {i}"),
                    "should_know",
                    "Fusion original",
                    i,
                    "2026-07-01",
                );
            }
            let results = search_shown_narratives(&conn, "fusion").unwrap();
            assert_eq!(results.len(), MAX_RESULTS);
        }
    }
}
