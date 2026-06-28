-- Per-run thread-processing health (sub-project C gate): record how many installments were
-- synthesized and -- critically -- how many had their faithfulness audit FAIL (fail-open,
-- keeping facts unchecked). A persistently non-zero audit_failures means bad facts are no
-- longer being dropped before they reach readers; it must be monitorable/alertable before
-- threads go reader-visible.

CREATE TABLE IF NOT EXISTS thread_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    threads_synthesized INTEGER NOT NULL DEFAULT 0,
    audit_failures INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_thread_runs_run_id ON thread_runs(run_id);
