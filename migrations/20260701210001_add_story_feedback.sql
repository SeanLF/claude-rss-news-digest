-- Per-story feedback: replaces the digest-level mailto "how was today's digest"
-- buttons with a one-click up/down vote per story, recorded via circulation's
-- GET /feedback route.
--
-- No user identity column -- there's no auth on this list, and reader identity
-- isn't needed to read the signal. `story` is the slugified headline computed
-- at render time (newsroom/src/render.py:slugify); both sides treat it as an
-- opaque string, not a foreign key.

CREATE TABLE IF NOT EXISTS story_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date TEXT NOT NULL,
    story TEXT NOT NULL,
    vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

CREATE INDEX IF NOT EXISTS idx_story_feedback_date ON story_feedback(digest_date);
