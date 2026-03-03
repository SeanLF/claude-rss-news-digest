# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend.

**Architecture:**
- `newsroom/` - Python pipeline (fetch, curate, render, email)
- `circulation/` - Rust web server for "View in browser" links and archive
- `data/` - Runtime data (SQLite database, logs, intermediate files)
- `migrations/` - Database schema migrations

**Curation pipeline (thin dispatcher + subagents):**

Claude never sees URLs. Python assigns opaque article IDs (A1, A2...) and builds `article_index.json`. A single `claude --print` invocation runs a thin dispatcher that orchestrates 5 file-based subagents:

1. **CLUSTER** -- group articles by story
2. **RECAP** -- summarise recent RSS titles (Haiku)
3. **SELECT** -- editorial judgment: tiers, regions, representative articles
4. **WRITE** -- headlines, summaries, why_it_matters (references article IDs only)
5. **COHERENCE** -- verify headlines vs source articles (Haiku)

Parent reads final output, drops failed coherence checks, calls `write_selections` MCP tool. Python then resolves article IDs to URLs/source/bias via `resolve_article_ids()` in `digest.py`.

**Intermediate files** (in `data/claude_input/`): `clusters.json`, `recap.txt`, `selected.json`, `draft_selections.json`, `coherence_report.json`, `article_index.json`.

**Dedup strategy:** TF-IDF pre-filter on RSS titles (not editorial). `recent_rss_titles.csv` + RECAP subagent + `weekly_recap.txt` replace the old `recent_headlines.csv` feedback loop.

## Commands

- **CI**: `bin/ci` - Always runs in Docker for reproducibility. Use `bin/ci --fix` to auto-fix style issues.
- **Tests only**: `docker compose run --rm --build ci pytest -v newsroom/tests/`
- **Migrate**: `bin/migrate` - Apply database migrations (runs in Docker)
- **Run digest**: `docker compose run --rm digest-newsroom` (default CMD: `.venv/bin/python src/run.py`; don't override with bare `python`)
- **Test prompts**: `bin/test-prompt run baseline --model opus` - Prompt experiment harness

## Database

SQLite at `data/digest.db`. Schema managed by migrations in `migrations/`.

**Tables:**
- `digest_runs` - run metadata (run_at, articles_fetched, completed_at, git_sha)
- `shown_narratives` - headlines shown with tier, source_id, and original_title (RSS title for dedup)
- `source_health` - feed fetch results for monitoring
- `digests` - HTML digest blobs keyed by date

**Migrations:**
- Run `bin/migrate` to apply pending migrations
- New migrations: `migrations/YYYYMMDDHHMMSS_description.sql`
- Production: `bin/ssh bin/migrate`

## Key Files

- `newsroom/src/run.py` - CLI + pipeline orchestration (delegates to focused modules)
- `newsroom/src/` - modules: config, feeds, prepare, claude, digest, render, broadcast, db, utils
- `.claude/commands/news-digest-select.md` - Thin dispatcher prompt (5 subagent orchestration)
- `newsroom/templates/digest-template.html` - HTML template for digest output
- `newsroom/templates/digest.css` - CSS styles (minified and injected at runtime)
- `newsroom/sources.json` - RSS feed definitions
- `circulation/` - Rust (Axum) web server for "View in browser" links and archive

## MCP Server

- Config: `newsroom/.mcp.json` - uses `.venv/bin/python` to access venv deps
- Schema validation via `jsonschema` rejects malformed tool calls (Claude retries). `SOURCE_SCHEMA` expects `{article_id}` only -- Python resolves to URLs post-Claude.
- If Claude says tool isn't available, check the Python path in `.mcp.json`

## Persistent TODO

Check `.claude/tasks/todo.md` for tasks that persist across sessions (not tracked by git).

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary
