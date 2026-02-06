# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend.

**Architecture:**
- `newsroom/` - Python pipeline (fetch, curate, render, email)
- `circulation/` - Rust web server for "View in browser" links and archive
- `data/` - Runtime data (SQLite database, logs, intermediate files)
- `migrations/` - Database schema migrations

## Commands

- **CI**: `bin/ci` - Always runs in Docker for reproducibility. Use `bin/ci --fix` to auto-fix style issues.
- **Tests only**: `docker compose run --rm --build ci pytest -v newsroom/tests/`
- **Migrate**: `bin/migrate` - Apply database migrations (runs in Docker)
- **Run digest**: `docker compose run --rm digest-newsroom`
- **Test prompts**: `bin/test-prompt run baseline --model opus` - Prompt experiment harness

## Database

SQLite at `data/digest.db`. Schema managed by migrations in `migrations/`.

**Tables:**
- `digest_runs` - run metadata (run_at, articles_fetched, completed_at, git_sha)
- `shown_narratives` - headlines shown with tier and source_id (7-day deduplication window)
- `source_health` - feed fetch results for monitoring
- `digests` - HTML digest blobs keyed by date

**Migrations:**
- Run `bin/migrate` to apply pending migrations
- New migrations: `migrations/YYYYMMDDHHMMSS_description.sql`
- Production: `bin/ssh bin/migrate`

## Key Files

- `newsroom/src/run.py` - CLI + pipeline orchestration (delegates to focused modules)
- `newsroom/src/` - modules: config, feeds, prepare, claude, digest, render, broadcast, db, utils
- `.claude/commands/news-digest-select.md` - Claude prompt for story selection
- `newsroom/templates/digest-template.html` - HTML template for digest output
- `newsroom/templates/digest.css` - CSS styles (minified and injected at runtime)
- `newsroom/sources.json` - RSS feed definitions
- `circulation/` - Rust (Axum) web server for "View in browser" links and archive

## MCP Server

- Config: `newsroom/.mcp.json` - uses `.venv/bin/python` to access venv deps
- Schema validation via `jsonschema` rejects malformed tool calls (Claude retries)
- If Claude says tool isn't available, check the Python path in `.mcp.json`

## Persistent TODO

Check `.claude/tasks/todo.md` for tasks that persist across sessions (not tracked by git).

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary
