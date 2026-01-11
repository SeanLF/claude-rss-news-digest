# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend.

Single file architecture: `run.py`. Runtime data in `data/`.

## Database

SQLite at `data/digest.db`:
- `digest_runs` - run metadata (run_at, articles_fetched, etc.)
- `shown_narratives` - headlines shown (7-day deduplication window)

## Key Files

- `run.py` - main pipeline (two-pass: select → write)
- `.claude/commands/news-digest-select.md` - Pass 1: story selection
- `.claude/commands/news-digest-write.md` - Pass 2: HTML generation
- `sources.json` - RSS feed definitions
- `digest.css` - CSS styles (minified and injected at runtime)

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary