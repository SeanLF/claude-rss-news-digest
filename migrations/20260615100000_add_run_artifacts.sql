-- Durable per-run trace store.
--
-- The per-run intermediates in data/claude_input/ (clusters.json, selected.json,
-- draft_selections.json, coherence_report.json, article_index.json, recap.txt) are
-- OVERWRITTEN every run, and the Claude session JSONLs live in a rotating Docker
-- volume. This table persists the full per-run trace in SQLite so it accumulates,
-- survives volume wipes/redeploys, grows the eval golden set, and lets us reproduce
-- any run.
--
-- Additive and backward-safe: one row per artifact, keyed by run. A synthetic
-- "models.json" artifact may also be stored to capture the resolved model IDs.

CREATE TABLE IF NOT EXISTS run_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    artifact_name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts(run_id);
