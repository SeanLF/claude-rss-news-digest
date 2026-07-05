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
# Canonical design tokens (repo-root design/, copied into the image). The digest
# email/web CSS references var(--…) from these; render prepends this file to
# digest.css so resolve_css_variables() can inline the tokens for email.
TOKENS_FILE = APP_DIR / "design" / "tokens.css"
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
# High-precision near-verbatim backstop. At the old 0.35 this hand-rolled title-TF-IDF filter fired
# on entity collisions: a 180-pair blind-judge study put its false-positive rate at 65%, and a
# counterfactual found ~23% of those drops were real world-news stories lost before Claude saw them
# (a Guinea-Bissau coup, deadly Kenya protests). At 0.80 it drops only genuine near-verbatim repeats
# (~100% precision on the labelled set); SELECT (yesterday_headlines) + THREADS own the nuanced
# semantic cross-day dedup. Full analysis: docs/2026-07-02-dedup-poc-findings.md.
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.80"))

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

# CLUSTER stage: the deterministic extract→join path (newsroom/src/cluster_extractjoin.py)
# REPLACES the old holistic Sonnet cluster.md subagent -- it produces cleaner, deterministic,
# less-repetitive partitions (validated in docs/2026-07-01-graph-gate-preregistration.md).
# Rollback is a code/image revert (no runtime flag, by design). These two knobs stay tunable:
# Sonnet extraction keeps source-diversity flat vs Haiku's ~10% dip (the A/B); join threshold
# 0.80 is the held-out value (the granularity-matching threshold rises with corpus size).
CLUSTER_EXTRACT_MODEL = os.environ.get("CLUSTER_EXTRACT_MODEL", "claude-sonnet-4-6")
CLUSTER_JOIN_THRESHOLD = float(os.environ.get("CLUSTER_JOIN_THRESHOLD", "0.80"))

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

# Full-text fetch for SELECTED stories (newsroom/src/fulltext.py): after SELECT, Python fetches
# each selected story's representative article pages (trafilatura) so WRITE/COHERENCE see full
# article text instead of the ~300-char RSS blurb. Strictly additive -- the step is wrapped so no
# exception can abort the run, and "no full text" falls back to the CSV summaries (the floor).
# FULLTEXT_ENABLED is the kill switch (same parsing style as THREADS_ENABLED) for a
# network-dependent step, e.g. a deploy target with restricted egress.
FULLTEXT_ENABLED = os.environ.get("FULLTEXT_ENABLED", "true").lower() in ("1", "true", "yes")
FULLTEXT_PER_STORY = int(os.environ.get("FULLTEXT_PER_STORY", "3"))
FULLTEXT_MAX_CHARS = int(os.environ.get("FULLTEXT_MAX_CHARS", "4000"))
FULLTEXT_DEADLINE_S = int(os.environ.get("FULLTEXT_DEADLINE_S", "120"))

# GNEWS_RESOLVE_ENABLED: kill switch for resolving Google-News redirect links (Reuters/Nikkei)
# to the publisher URL at render time. Best-effort + undocumented Google internals, so keep it
# trivially disableable if Google breaks the RPC (the gnews_live canary flags that).
GNEWS_RESOLVE_ENABLED = os.environ.get("GNEWS_RESOLVE_ENABLED", "true").lower() in ("1", "true", "yes")
GNEWS_RESOLVE_TIMEOUT_S = int(os.environ.get("GNEWS_RESOLVE_TIMEOUT_S", "15"))
# Serial pace between requests to news.google.com. Google sends no Retry-After and 429s the whole
# IP under bursts; at ~10-30 requests/run once daily a 2s pace is ~10x under the abuse threshold.
GNEWS_RESOLVE_DELAY_S = float(os.environ.get("GNEWS_RESOLVE_DELAY_S", "2"))
# Wall-clock budget for the whole resolve pass (~11-16 links/run take ~70-100s at the pace above);
# a slow Google day can't stall the digest -- unresolved links just keep their raw GN URL.
GNEWS_RESOLVE_DEADLINE_S = int(os.environ.get("GNEWS_RESOLVE_DEADLINE_S", "120"))
