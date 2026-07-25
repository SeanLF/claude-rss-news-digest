# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend.

**Architecture:**
- `newsroom/` - Python pipeline (fetch, curate, render, email)
- `circulation/` - Rust web server for "View in browser" links and archive
- `data/` - Runtime data (SQLite database, logs, intermediate files)
- `migrations/` - Database schema migrations

**Curation pipeline (Python-orchestrated subagents):**

Claude never sees URLs. Python assigns opaque article IDs (A1, A2...) and builds `article_index.json`. `orchestrate.py` runs 5 file-based subagents deterministically in a fixed order, invoking each one through the Claude Agent SDK wrapper (`claude_cli.py`):

1. **CLUSTER** -- group articles by story
2. **RECAP** -- summarise recent RSS titles (Haiku)
3. **SELECT** -- editorial judgment: tiers, regions, representative articles
4. **WRITE** -- headlines, summaries, why_it_matters (references article IDs only)
5. **COHERENCE** -- verify headlines vs source articles (Haiku)

After the stages complete, Python (`merge.py:assemble_selections`) reads `draft_selections.json` and `coherence_report.json`, drops headlines whose coherence entry has `pass: false`, validates against `schema.SELECTIONS_SCHEMA`, and writes `selections.json`. Python then resolves article IDs to URLs/source/bias via `resolve_article_ids()` in `digest.py`.

**Intermediate files** (in `data/claude_input/`): `clusters.json`, `recap.txt`, `selected.json`, `article_fulltext.json` (Python-fetched full text for SELECTED stories, best-effort), `draft_selections.json`, `coherence_report.json`, `article_index.json`, `selections.json` (assembled by Python).

**Dedup strategy:** TF-IDF pre-filter on RSS titles (not editorial). `recent_rss_titles.csv` + RECAP subagent + `weekly_recap.txt` replace the old `recent_headlines.csv` feedback loop.

## Commands

Run `make help` for the full list. Key commands:
- **CI**: `make ci` (all checks in Docker), `make ci-fix` (auto-fix), `make ci-full` (+ cargo audit)
- **Tests only**: `make test`
- **Deploy**: `make deploy` (full pipeline), `make deploy-dry` (preview)
- **Migrate**: `make migrate`, `make migrate-status`
- **Database**: `make db-clone` (pull prod DB), `make usage` / `make usage-daily`
- **Server**: `make ssh`
- **Run digest**: `docker compose run --rm digest-newsroom` (entrypoint passes flags to `run.py`, e.g. `--dry-run`)
- **Test prompts**: `make prompt NAME=baseline`
- **Versions**: `make versions`

## Database

SQLite at `data/digest.db`. Schema managed by migrations in `migrations/`.

**Tables:**
- `digest_runs` - run metadata (run_at, articles_fetched, completed_at, git_sha)
- `shown_narratives` - headlines shown with tier, source_id, and original_title (RSS title for dedup)
- `source_health` - feed fetch results for monitoring
- `digests` - HTML digest blobs keyed by date
- `run_usage` - per-subagent token usage and API-equivalent costs per run

**Migrations:**
- Applied automatically on each run via `db.init()`
- `make migrate-status` / `make migrate` for inspection and application
- New migrations: `migrations/YYYYMMDDHHMMSS_description.sql`
- Production: also auto-applied; `make ssh` then `bin/migrate` for manual use

## Key Files

- `newsroom/src/run.py` - CLI + pipeline orchestration (delegates to focused modules)
- `newsroom/src/` - modules: config, feeds, prepare, claude, digest, render, broadcast, db, usage, utils
- `newsroom/src/orchestrate.py` - Python orchestration of the 5 curation stages (replaced the old `/news-digest-select` LLM dispatcher); reads `.claude/agents/*.md`
- `newsroom/src/merge.py` - post-orchestration assembly (drop coherence-failed entries, validate, write selections.json)
- `newsroom/src/schema.py` - SELECTIONS_SCHEMA used to validate the assembled output
- `newsroom/templates/digest-template.html` - HTML template for digest output
- `newsroom/templates/digest.css` - CSS styles (minified and injected at runtime)
- `newsroom/sources.json` - RSS feed definitions
- `circulation/` - Rust (Axum) web server for "View in browser" links and archive

## Module Layering

`newsroom/src/` imports flow one direction. Do not introduce a cycle.

```
config, schema          no internal imports — keep them leaf modules
  -> db, feeds, utils
  -> render, merge, repair
  -> prepare, digest, orchestrate
  -> claude
  -> run                entry point; the only module that may import broadly
```

`run.py` is the CLI and may import anything. Everything else imports downward
only. If a low-level module needs something from a higher one, the dependency
is pointing the wrong way — pass it in instead.

## Working Docs

- `docs/solutions/` — reusable lessons, one per file, named for the lesson.
  Write one when closing an incident or landing a non-obvious fix, as its own
  commit. Convention in `docs/solutions/README.md`.
- `docs/operations.md` — command reference and environment notes.
- `docs/postmortems/` — incident narratives.
- `docs/` (dated files) — design docs, evals, handoffs.

## Persistent TODO

Check `.claude/tasks/todo.md` for tasks that persist across sessions (not tracked by git).

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary or fetched article text
