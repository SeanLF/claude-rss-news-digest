"""Centralized configuration for the news digest pipeline.

All paths, thresholds, and environment-based settings in one place.
"""

import os
from pathlib import Path

# =============================================================================
# Paths
# =============================================================================

APP_DIR = Path("/app")
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "digest.db"
LOG_FILE = DATA_DIR / "digest.log"
FETCHED_DIR = DATA_DIR / "fetched"
OUTPUT_DIR = DATA_DIR / "output"
CLAUDE_INPUT_DIR = DATA_DIR / "claude_input"
SOURCES_FILE = APP_DIR / "sources.json"
STYLES_FILE = APP_DIR / "templates" / "digest.css"
TEMPLATE_FILE = APP_DIR / "templates" / "digest-template.html"
MIGRATIONS_DIR = APP_DIR / "migrations"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-app"

# =============================================================================
# RSS Fetching
# =============================================================================

MAX_RETRIES = int(os.environ.get("RSS_MAX_RETRIES", "3"))
RETRY_DELAY = int(os.environ.get("RSS_RETRY_DELAY", "2"))
HEALTH_ALERT_THRESHOLD = int(os.environ.get("HEALTH_ALERT_THRESHOLD", "3"))

# =============================================================================
# Article Processing
# =============================================================================

MAX_TOKENS_PER_FILE = 10000  # Conservative limit for Claude Code file reading
MAX_ARTICLES_FOR_DRY_RUN = 20  # Limit articles during dry runs
MAX_TITLE_LENGTH = 500
MAX_SUMMARY_LENGTH = 200

# =============================================================================
# Deduplication
# =============================================================================

DEDUP_WINDOW_DAYS = 7  # Days of headline history for deduplication
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.35"))

# =============================================================================
# Models
# =============================================================================
# Per-stage curation models live in each agent's .claude/agents/<name>.md
# frontmatter, and --model overrides every stage at runtime. These two cover the
# choices that aren't stage-bound: the wrapper default (used by the auth health
# check and as the artifact-attribution fallback) and the standalone weekly recap
# (a separate Haiku call, distinct from the per-run RECAP stage). Env-overridable
# so a deploy can change models without a code change.

DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")
RECAP_MODEL = os.environ.get("RECAP_MODEL", "claude-haiku-4-5")

# Evolving story-thread substrate (sub-project A). Off by default until the feature is
# reader-visible (sub-project C); when on, each run persists thread identity for the
# selected stories. THREAD_DORMANT_AFTER: runs since last-seen before a thread stops matching.
THREADS_ENABLED = os.environ.get("THREADS_ENABLED", "false").lower() in ("1", "true", "yes")
THREAD_DORMANT_AFTER = int(os.environ.get("THREAD_DORMANT_AFTER", "3"))
# Late-binding (sub-project D): widen a thread's synthesis input from its matched cluster to the
# entity-soft neighbourhood across the run's clusters (pulls scattered facets of the same story).
# Off by default; an enhancer to B, not required for the feature.
THREAD_LATEBIND = os.environ.get("THREAD_LATEBIND", "false").lower() in ("1", "true", "yes")
THREAD_LATEBIND_THRESHOLD = float(os.environ.get("THREAD_LATEBIND_THRESHOLD", "0.35"))
THREAD_LATEBIND_MAX_EXTRA = int(os.environ.get("THREAD_LATEBIND_MAX_EXTRA", "12"))
