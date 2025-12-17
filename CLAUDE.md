# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → plain text email.

Single file architecture: everything is in `run.py` (~300 lines).

## File Layout

```
run.py sections:
├── Configuration    # Paths, RSS sources list
├── Utilities        # Logging, internet check
├── Database         # Schema, init, last run time
├── RSS Fetching     # Parallel feed fetching
├── Digest Generation # Claude invocation
├── Email            # SMTP sending
└── Main Pipeline    # Orchestration
```

Runtime data in `data/`: digest.db, fetched/, output/, digest.log

## When Modifying Code

- All logic is in `run.py` - edit there
- `run-digest.sh` just loads .env and runs Docker - keep minimal
- Only external dependency is `feedparser` (in Docker)
- Docker uses `claude-config/commands/news-digest.md`, local uses `~/.claude/commands/news-digest.md`

## When Running the Digest

1. Always fetch first (done automatically by `run.py`)
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

SQLite at `data/digest.db`. Two tables:
- `digest_runs` - metadata per run
- `shown_narratives` - headlines shown (for deduplication)

## Testing

```bash
# Test full Docker flow
./run-digest.sh

# Test locally (requires feedparser, claude CLI)
python run.py
```

## Don't

- Don't add dependencies beyond feedparser
- Don't generate HTML (plain text only)
- Don't skip deduplication
- Don't hardcode paths or emails
