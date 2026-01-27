# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend.

Single file architecture: `run.py`. Runtime data in `data/`.

## Commands

- **CI**: `bin/ci` - Always runs in Docker for reproducibility. Use `bin/ci --fix` to auto-fix style issues.
- **Tests only**: `docker compose run --rm --build ci pytest -v`
- **Run digest**: `docker compose run --rm news-digest`

## Database

SQLite at `data/digest.db`:

- `digest_runs` - run metadata (run_at, articles_fetched, etc.)
- `shown_narratives` - headlines shown (7-day deduplication window)
- `digest_source_usage` - tracks which sources contributed to each run (by tier)
- `source_health` - feed fetch results for monitoring

## Key Files

- `run.py` - main pipeline (two-pass: select → write)
- `.claude/commands/news-digest-select.md` - Pass 1: story selection
- `.claude/commands/news-digest-write.md` - Pass 2: HTML generation
- `sources.json` - RSS feed definitions
- `digest.css` - CSS styles (minified and injected at runtime)

## MCP Server

- Config: `.mcp.json` - uses `.venv/bin/python` to access venv deps
- Schema validation via `jsonschema` rejects malformed tool calls (Claude retries)
- If Claude says tool isn't available, check the Python path in `.mcp.json`

## Persistent TODO

Check `.claude/tasks/todo.md` for tasks that persist across sessions (not tracked by git).

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary
