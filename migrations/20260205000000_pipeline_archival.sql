-- Pipeline data archival for analysis and experimentation
-- Stores all fetched articles and Claude selections for historical queries

-- All fetched articles (before TF-IDF filtering)
CREATE TABLE IF NOT EXISTS fetched_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published TEXT,
    summary TEXT,
    fetched_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);

-- Claude's raw selection output
CREATE TABLE IF NOT EXISTS selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    selections_json TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_fetched_articles_run ON fetched_articles(run_id);
CREATE INDEX IF NOT EXISTS idx_fetched_articles_source ON fetched_articles(source_id);
CREATE INDEX IF NOT EXISTS idx_fetched_articles_date ON fetched_articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_selections_run ON selections(run_id);
