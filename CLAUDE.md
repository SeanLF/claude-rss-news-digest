# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → email delivery.

## When Modifying Code

- `fetch_feeds.py` uses `uv run` - don't add pip dependencies
- `send_email.py` uses only stdlib (smtplib) - keep it that way
- `run-digest.sh` must work headless from cron - no interactive prompts
- Docker runs use `claude-config/commands/news-digest.md`, local uses `~/.claude/commands/news-digest.md`

## When Running the Digest

1. Always fetch first: `uv run python fetch_feeds.py`
2. Always check deduplication: query `shown_narratives` for last 7 days
3. Always record what you show: insert into `shown_narratives` after generating
4. Local output: HTML to `digest-*.html`
5. Docker output: plain text to `digest-*.txt`

## Content Curation Rules

- Never fabricate details beyond RSS title/summary
- Never repeat stories from last 7 days unless major development (mark [UPDATE])
- Filter out: celebrity, sports, lifestyle, US domestic policy (unless international impact)
- Prioritize: geopolitics, tech/AI, privacy, France/Canada news
- Notice geographic gaps - don't default to US angle

## Database

SQLite at `digest.db`. Two tables:
- `digest_runs` - metadata per run
- `shown_narratives` - headlines shown (for deduplication)

Always record shown narratives after generating a digest.

## Testing Changes

```bash
# Test feed fetching
uv run python fetch_feeds.py

# Test email (create a test file first)
echo "test" > /tmp/test.txt && python3 send_email.py /tmp/test.txt

# Test full flow
./run-digest.sh
```

## Don't

- Don't add external Python dependencies
- Don't use HTML output in Docker (plain text only)
- Don't skip the deduplication check
- Don't hardcode email addresses (use DIGEST_EMAIL env var)
