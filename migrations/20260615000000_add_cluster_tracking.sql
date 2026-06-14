-- Persist cluster/story assignments so daily overlap/redundancy can be measured.
--
-- Two additive, backward-safe changes:
--   1. cluster_id (TEXT, nullable) on shown_narratives links each shown headline
--      to its CLUSTER subagent story label. Existing rows stay NULL.
--   2. cluster_runs archives the full per-run clusters.json blob, since
--      data/claude_input/clusters.json is overwritten every run. This makes
--      historical cluster composition queryable (one row per run).

ALTER TABLE shown_narratives ADD COLUMN cluster_id TEXT;

CREATE TABLE IF NOT EXISTS cluster_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    clusters_json TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_cluster_runs_run_id ON cluster_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_shown_narratives_cluster_id ON shown_narratives(cluster_id);
