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
