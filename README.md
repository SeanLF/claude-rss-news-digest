# News Digest

Automated daily news digest powered by Claude. Fetches from 28 balanced RSS sources, deduplicates against recent history, clusters into narratives, and emails a curated summary.

## How It Works

1. **Fetch** - Python script pulls RSS feeds from 28 sources into JSON files
2. **Process** - Claude reads all articles, deduplicates against last 7 days, filters noise, clusters related stories
3. **Generate** - Outputs plain text digest with tiered stories (Must Know → Should Know → Quick Signals)
4. **Email** - Sends via SMTP to configured recipients
5. **Record** - Stores shown headlines in SQLite for future deduplication

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  RSS Feeds  │────▶│ fetch_feeds │────▶│data/fetched/│
│  (28 src)   │     │   .py       │     │  *.json     │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
┌─────────────┐     ┌─────────────┐     ┌──────▼──────┐
│   Email     │◀────│ send_email  │◀────│ Claude Code │
│  (SMTP)     │     │   .py       │     │  (Docker)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                                        ┌──────▼──────┐
                                        │data/digest  │
                                        │    .db      │
                                        └─────────────┘
```

## Setup

### Prerequisites

- Docker (OrbStack, Docker Desktop, or native)
- Anthropic API key

### Configuration

1. Copy environment template:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-...

   # SMTP (Gmail example - use App Password)
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=you@gmail.com
   SMTP_PASS=xxxx-xxxx-xxxx-xxxx

   # Recipients (comma-separated)
   DIGEST_EMAIL=you@example.com,friend@example.com
   ```

### Gmail App Password

1. Enable 2FA on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Generate an app password for "Mail"
4. Use that 16-character password as `SMTP_PASS`

### Cron Setup

Add to crontab (`crontab -e`):

```bash
# Daily at 07:00 UTC (adjust for your timezone)
0 7 * * * /path/to/news-digest/run-digest.sh >> /path/to/news-digest/data/cron.log 2>&1
```

The container handles:
- Internet connectivity check (skips if offline)
- Database initialization on first run
- Logging to `data/digest.log`

## Manual Run

```bash
# Via Docker (recommended)
./run-digest.sh

# Interactive (with Claude Code locally installed)
cd /path/to/news-digest && uv run python -c "from run import fetch_feeds; fetch_feeds()"
claude -p /news-digest
```

## File Structure

```
news-digest/
├── run.py                  # Everything: fetch, DB, email, pipeline (~300 lines)
├── run-digest.sh           # Host entry: loads .env, runs Docker
├── Dockerfile              # Container: Python + Claude CLI + feedparser
├── docker-compose.yml      # Container orchestration
├── .env                    # Configuration (git-ignored)
├── .env.example            # Config template
├── claude-config/          # Claude config for Docker
│   └── commands/
│       └── news-digest.md  # Slash command definition
└── data/                   # All runtime data (git-ignored)
    ├── digest.db           # SQLite (runs, shown narratives)
    ├── digest.log          # Execution logs
    ├── fetched/            # RSS JSON cache
    └── output/             # Generated digests
```

## Sources (28)

| Category | Sources | Bias |
|----------|---------|------|
| Wire | Reuters, AP, AFP | center |
| Canadian | Globe and Mail, CBC | center |
| UK/US | Guardian, NYT, WaPo | center-left |
| Finance | FT, Economist, WSJ | center-right |
| Tech | HN, Ars Technica, The Verge, Rest of World | center |
| Asia-Pacific | SCMP (3), Nikkei Asia, Straits Times | center |
| Middle East | Al Jazeera | center |
| Europe | Le Monde, Der Spiegel | center/center-left |
| India | The Hindu | center |
| Africa | Daily Maverick | center-left |
| Philippines | Rappler | center |
| Investigative | ProPublica, The Intercept | center-left/left |

### Known Gaps

- **Latin America** - No sources
- **Eastern Europe** - No sources from inside region
- **Climate** - No dedicated environmental sources
- **Right-leaning** - WSJ/Economist only; no populist-right

## Key Design Decisions

### Plain Text Only
HTML digests bloated Claude's context window. Plain text is smaller, readable everywhere, and simpler to generate.

### 7-Day Deduplication
Headlines are stored in SQLite and checked against new articles. Stories only repeat if there's a major development (marked [UPDATE]).

### Docker-First
Everything runs in a single container - Python, feedparser, Claude CLI. No local dependencies beyond Docker. Works on macOS, Linux, or cloud.

### Internet Check
Container pings Anthropic API before running. If offline, exits cleanly - useful when traveling or on spotty connections.

### Tiered Output
- **Must Know (2-4)**: Stories you'd be embarrassed not to know
- **Should Know (3-6)**: Important but not urgent
- **Quick Signals (5-10)**: One-liners worth tracking

## Troubleshooting

### "No digest generated"
- Check `data/digest.log` for errors
- Verify `ANTHROPIC_API_KEY` is set in `.env`
- Try running manually: `./run-digest.sh`

### Email not sending
- Verify SMTP credentials in `.env`
- For Gmail, ensure you're using an App Password (not regular password)
- Check `data/digest.log` for SMTP errors

### Stale/repeated stories
- Check database for recent entries:
  ```bash
  sqlite3 data/digest.db "SELECT * FROM shown_narratives ORDER BY shown_at DESC LIMIT 20;"
  ```

### Container build issues
- Rebuild without cache: `docker compose build --no-cache`
- Check Docker is running: `docker info`
