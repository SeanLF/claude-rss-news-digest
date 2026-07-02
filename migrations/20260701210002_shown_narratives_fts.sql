-- Full-text search over shown headlines (archive search).
--
-- External-content FTS5 table backed by shown_narratives: indexes headline and
-- original_title without duplicating the row data, per the documented
-- rowid-linked pattern (https://sqlite.org/fts5.html#external_content_tables).
-- FTS5 availability confirmed in both consumers before writing this: rusqlite
-- 0.39 (bundled libsqlite3-sys 0.37) and the newsroom container's uv-installed
-- Python 3.14 (sqlite3 3.53.1) both build CREATE VIRTUAL TABLE ... USING fts5
-- successfully.
--
-- shown_narratives is append-only in normal operation (record_shown_headlines
-- in db.py only INSERTs), but delete_run() DELETEs by run_id, so a DELETE
-- trigger keeps the index correct rather than assuming that path is dead.
-- The UPDATE trigger below is the same insurance: nothing in the codebase
-- UPDATEs shown_narratives today, but without it a future update path would
-- silently desync the FTS index forever rather than fail loudly.

CREATE VIRTUAL TABLE IF NOT EXISTS shown_narratives_fts USING fts5(
    headline,
    original_title,
    content='shown_narratives',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS shown_narratives_fts_insert AFTER INSERT ON shown_narratives BEGIN
    INSERT INTO shown_narratives_fts(rowid, headline, original_title)
    VALUES (new.id, new.headline, new.original_title);
END;

CREATE TRIGGER IF NOT EXISTS shown_narratives_fts_delete AFTER DELETE ON shown_narratives BEGIN
    INSERT INTO shown_narratives_fts(shown_narratives_fts, rowid, headline, original_title)
    VALUES ('delete', old.id, old.headline, old.original_title);
END;

-- Insurance for a write path that doesn't exist yet: no code UPDATEs
-- shown_narratives today, but if one is added later this keeps the index in
-- sync instead of silently drifting. Standard external-content delete+insert
-- idiom (https://sqlite.org/fts5.html#external_content_tables).
CREATE TRIGGER IF NOT EXISTS shown_narratives_fts_update AFTER UPDATE ON shown_narratives BEGIN
    INSERT INTO shown_narratives_fts(shown_narratives_fts, rowid, headline, original_title)
    VALUES ('delete', old.id, old.headline, old.original_title);
    INSERT INTO shown_narratives_fts(rowid, headline, original_title)
    VALUES (new.id, new.headline, new.original_title);
END;

-- Backfill rows that existed before this migration.
INSERT INTO shown_narratives_fts(rowid, headline, original_title)
SELECT id, headline, original_title FROM shown_narratives;
