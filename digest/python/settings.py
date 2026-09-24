"""The fetch's four settings, read from the environment with newsroom/src/config.py's defaults.

Planning (FULLTEXT_ENABLED, FULLTEXT_PER_STORY) belongs to the TypeScript side, which decides what to fetch.
"""

import os

FULLTEXT_MAX_CHARS = int(os.environ.get("FULLTEXT_MAX_CHARS", "4000"))
# The child's soft budget. It bounds the waiter, not the work; FULLTEXT_KILL_GRACE_S on top of it is the bound.
# docs/lessons/a-deadline-on-the-waiter-does-not-bound-the-worker.md
FULLTEXT_DEADLINE_S = int(os.environ.get("FULLTEXT_DEADLINE_S", "120"))
FULLTEXT_KILL_GRACE_S = int(os.environ.get("FULLTEXT_KILL_GRACE_S", "30"))
# Pre-parse cap on a single document, in decoded characters (0 disables). Trims the slow tail; not a bound.
FULLTEXT_MAX_DOC_CHARS = int(os.environ.get("FULLTEXT_MAX_DOC_CHARS", "2000000"))
