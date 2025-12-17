# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → plain text email.

## File Paths

All runtime data lives in `data/`:
- `data/digest.db` - SQLite database
- `data/fetched/` - RSS JSON cache
- `data/output/` - Generated digests
- `data/digest.log` - Execution logs

In Docker, paths are `/app/data/*`. Locally, paths are relative to repo root.

## When Modifying Code

- `run.py` orchestrates the full pipeline (fetch → Claude → email) - keep it simple
- `fetch_feeds.py` uses `feedparser` (installed in Docker) - no other dependencies
- `send_email.py` uses only stdlib (smtplib) - keep it that way
- `run-digest.sh` just loads .env and runs Docker - minimal shell
- Docker uses `claude-config/commands/news-digest.md`, local uses `~/.claude/commands/news-digest.md`

## When Running the Digest

1. Always fetch first: `python fetch_feeds.py` (or `uv run` locally)
2. Always check deduplication: query `shown_narratives` for last 7 days
3. Always record what you show: insert into `shown_narratives` after generating
4. Output: plain text to `data/output/digest-*.txt`

## Content Curation Rules

- Never fabricate details beyond RSS title/summary
- Never repeat stories from last 7 days unless major development (mark [UPDATE])
- Filter out: celebrity, sports, lifestyle, US domestic policy (unless international impact)
- Prioritize: geopolitics, tech/AI, privacy, France/Canada news
- Notice geographic gaps - don't default to US angle

## Database

SQLite at `data/digest.db`. Two tables matter:
- `digest_runs` - metadata per run
- `shown_narratives` - headlines shown (for deduplication)

Always record shown narratives after generating a digest.

## Testing Changes

```bash
# Test feed fetching
uv run python fetch_feeds.py

# Test email (create a test file first)
echo "test" > /tmp/test.txt && python3 send_email.py /tmp/test.txt

# Test full Docker flow
./run-digest.sh
```

## Don't

- Don't add external Python dependencies beyond feedparser
- Don't generate HTML output (plain text only)
- Don't skip the deduplication check
- Don't hardcode paths (use data/ relative paths)
- Don't hardcode email addresses (use DIGEST_EMAIL env var)
