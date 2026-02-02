-- Baseline migration: schema as of 2026-01-01
-- For existing databases: mark as applied with `bin/migrate --mark 20260101000000_baseline`
-- For new databases: creates all tables

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at DATETIME DEFAULT (datetime('now', 'utc')),
    articles_fetched INTEGER,
    articles_emailed INTEGER
);

CREATE TABLE IF NOT EXISTS shown_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT NOT NULL,
    tier TEXT,
    source_id TEXT,
    shown_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS source_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT,
    articles_fetched INTEGER,
    articles_kept INTEGER,
    recorded_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS digests (
    date TEXT PRIMARY KEY,
    html TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS dedup_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at DATETIME DEFAULT (datetime('now', 'utc')),
    article_title TEXT NOT NULL,
    article_source_id TEXT,
    matched_headline TEXT NOT NULL,
    similarity REAL NOT NULL,
    threshold REAL NOT NULL,
    action TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shown_narratives_date ON shown_narratives(shown_at);
CREATE INDEX IF NOT EXISTS idx_shown_narratives_source ON shown_narratives(source_id);
CREATE INDEX IF NOT EXISTS idx_digest_runs_date ON digest_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_source_health_source ON source_health(source_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(date);
CREATE INDEX IF NOT EXISTS idx_dedup_log_date ON dedup_log(logged_at);
