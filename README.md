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
│  RSS Feeds  │────▶│ fetch_feeds │────▶│  fetched/   │
│  (28 src)   │     │   .py       │     │  *.json     │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
┌─────────────┐     ┌─────────────┐     ┌──────▼──────┐
│   Email     │◀────│ send_email  │◀────│ Claude Code │
│  (SMTP)     │     │   .py       │     │  (Docker)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                                        ┌──────▼──────┐
                                        │  digest.db  │
                                        │  (SQLite)   │
                                        └─────────────┘
```

## Setup

### Prerequisites

- [OrbStack](https://orbstack.dev/) (or Docker Desktop)
- Python 3.9+ with `uv` package manager
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
0 7 * * * /Users/sean/Developer/news-digest/run-digest.sh 2>&1
```

The script handles:
- Internet connectivity check (skips if offline)
- OrbStack lifecycle (starts if needed, stops after if it wasn't running)
- Logging to `digest.log`

## Manual Run

```bash
# Interactive (with Claude Code locally)
claude -p /news-digest

# Via Docker
./run-digest.sh
```

## File Structure

```
news-digest/
├── fetch_feeds.py          # RSS fetcher
├── send_email.py           # SMTP sender
├── run-digest.sh           # Cron entry script
├── docker-compose.yml      # Claude Code container
├── digest.db               # SQLite (runs, shown narratives)
├── digest.css              # Styles (for HTML output)
├── .env                    # Configuration (git-ignored)
├── .env.example            # Config template
├── fetched/                # RSS JSON cache
│   ├── _metadata.json      # Source names, bias, perspective
│   └── *.json              # Per-source articles
├── claude-config/          # Claude config for Docker
│   └── commands/
│       └── news-digest.md  # Slash command (plain text version)
└── output/                 # Generated digests (gitignored)
    ├── digest-*.html       # HTML (local runs)
    └── digest-*.txt        # Plain text (Docker/email)
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

### Plain Text Email
HTML digests were large and bloated conversation context. Plain text is:
- Smaller (better for Claude's context)
- Readable everywhere
- Simpler to generate

### 7-Day Deduplication
Headlines are stored in SQLite and checked against new articles. Stories only repeat if there's a major development (marked [UPDATE]).

### OrbStack Lifecycle
Script detects if OrbStack was running before execution. If not, it starts it and stops it after - preserving system resources when not in use.

### Internet Check
Pings Anthropic API before running. If offline, exits cleanly without error - useful when traveling or on spotty connections.

### Tiered Output
- **Must Know (2-4)**: Stories you'd be embarrassed not to know
- **Should Know (3-6)**: Important but not urgent
- **Quick Signals (5-10)**: One-liners worth tracking

## Troubleshooting

### "No digest generated"
- Check `digest.log` for errors
- Verify `ANTHROPIC_API_KEY` is set
- Try running manually: `./run-digest.sh`

### OrbStack won't start
- Check if OrbStack is installed: `ls /Applications/OrbStack.app`
- Try starting manually: `open -a OrbStack`

### Email not sending
- Verify SMTP credentials in `.env`
- For Gmail, ensure you're using an App Password (not regular password)
- Check `digest.log` for SMTP errors

### Stale/repeated stories
- Check `digest.db` for recent entries:
  ```bash
  sqlite3 digest.db "SELECT * FROM shown_narratives ORDER BY shown_at DESC LIMIT 20;"
  ```
