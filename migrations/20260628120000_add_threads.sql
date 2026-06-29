-- Evolving story-thread substrate (sub-project A): persistent identity + carried
-- state for ongoing stories tracked across daily runs.
--
-- threads            : one row per ongoing story (identity + latest label).
-- thread_questions   : the open-question ledger -- raised, then marked resolved.
-- thread_installments: per-run record -- which selected story matched, and the synthesized
--                      content (whats_new facts), which is the thread's running memory.

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT,
    label TEXT NOT NULL,                      -- latest story label (shown to the linker next run)
    status TEXT NOT NULL DEFAULT 'active',   -- active | dormant | closed
    first_run_id INTEGER,
    last_run_id INTEGER,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    updated_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (first_run_id) REFERENCES digest_runs(id),
    FOREIGN KEY (last_run_id) REFERENCES digest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);

CREATE TABLE IF NOT EXISTS thread_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | resolved
    raised_run_id INTEGER,
    resolved_run_id INTEGER,
    resolved_how TEXT,
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);
CREATE INDEX IF NOT EXISTS idx_thread_questions_thread ON thread_questions(thread_id, status);

CREATE TABLE IF NOT EXISTS thread_installments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    cluster_story TEXT,
    matched_score REAL,                       -- 1.0 for a linker continuation, NULL for a new thread
    created_at DATETIME DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (thread_id) REFERENCES threads(id),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_thread_installments_run ON thread_installments(run_id);
