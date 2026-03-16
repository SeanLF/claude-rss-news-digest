-- Track per-subagent token usage from Claude session JSONL files.
-- API-equivalent costs (actual cost is $0 on subscription).
-- depends: 20260310100000_backfill_preheaders

CREATE TABLE IF NOT EXISTS run_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES digest_runs(id),
    subagent TEXT NOT NULL,  -- 'dispatcher', 'cluster', 'recap', 'select', 'write', 'coherence'
    model TEXT NOT NULL,     -- e.g. 'claude-sonnet-4-6'
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    api_cost_usd REAL NOT NULL DEFAULT 0.0,
    recorded_at DATETIME DEFAULT (datetime('now', 'utc'))
);

CREATE INDEX IF NOT EXISTS idx_run_usage_run_id ON run_usage(run_id);
