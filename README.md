# News Digest

Automated daily news digest powered by Claude. Fetches from 28 balanced RSS sources, deduplicates against recent history, clusters into narratives, and emails a curated HTML summary.

## How It Works

1. **Fetch** - Python script pulls RSS feeds from 28 sources, filters by last run time
2. **Prepare** - Splits articles into CSV files (~10k tokens each) for Claude to read
3. **Curate** - Claude reads all articles, deduplicates, filters noise, clusters stories
4. **Generate** - Outputs HTML digest with tiered stories and regional clusters
5. **Email** - Sends via SMTP to configured recipients
6. **Record** - Stores shown headlines in SQLite for 7-day deduplication

## Quick Start

### Prerequisites

- Python 3.9+
- [Claude Code CLI](https://github.com/anthropics/claude-code) installed and authenticated
- SMTP credentials (Gmail with App Password works well)

### Setup

```bash
# Clone the repo
git clone https://github.com/yourusername/news-digest.git
cd news-digest

# Install Python dependency
pip install feedparser

# Copy the slash command to your Claude config
mkdir -p ~/.claude/commands
cp .claude/commands/news-digest.md ~/.claude/commands/

# Create your .env file
cp .env.example .env
# Edit .env with your SMTP credentials and recipient emails
```

### Configuration (.env)

```bash
# SMTP settings (Gmail example - use App Password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=xxxx-xxxx-xxxx-xxxx  # App Password, not regular password

# Recipients (comma-separated)
DIGEST_EMAIL=you@example.com,friend@example.com

# Optional
DIGEST_NAME=Your Daily Digest  # Display name
```

### Gmail App Password

1. Enable 2FA on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Generate an app password for "Mail"
4. Use that 16-character password as `SMTP_PASS`

### Run

```bash
# Set environment variables
source .env  # Or export them manually

# Full run: fetch, generate, email, record
python run.py

# Dry run (no email, no DB record)
python run.py --dry-run

# Generate and record, but skip email
python run.py --no-email

# Generate and email, but skip DB record
python run.py --no-record

# Test SMTP config
python run.py --test-email
```

## Docker Setup (Automated/Cron)

For unattended runs, use Docker:

```bash
# Build and run
./run-digest.sh

# Or with docker compose
docker compose up --build
```

### Cron Setup

Add to crontab (`crontab -e`):

```bash
# Daily at 07:00 UTC
0 7 * * * /path/to/news-digest/run-digest.sh >> /path/to/news-digest/data/cron.log 2>&1
```

## File Structure

```
news-digest/
├── run.py                  # Main pipeline (~400 lines)
├── sources.json            # RSS feed definitions (28 sources)
├── run-digest.sh           # Docker entry script
├── Dockerfile              # Container definition
├── docker-compose.yml      # Container orchestration
├── .env                    # Configuration (git-ignored)
├── .env.example            # Config template
├── CLAUDE.md               # Instructions for Claude
├── .claude/commands/       # Claude slash command (used by both local and Docker)
│   └── news-digest.md
└── data/                   # Runtime data (git-ignored)
    ├── digest.db           # SQLite (runs, shown narratives)
    ├── digest.log          # Execution logs
    ├── claude_input/       # CSV files for Claude
    ├── fetched/            # RSS JSON cache
    └── output/             # Generated HTML digests
```

## Output Format

The digest is an HTML email with:

- **Regional Summary** - Quick overview by region (Americas, Europe, Asia-Pacific, ME&Africa, Tech)
- **Must Know (3-4)** - Stories you'd be embarrassed not to know, with "Why it matters"
- **Should Know (5-8)** - Important but not urgent
- **Quick Signals (10-15)** - One-liners worth tracking
- **Below the Fold** - Regional clusters with emoji headers for remaining stories

Supports dark mode automatically.

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

## Key Design Decisions

### HTML Output
Rich formatting with regional summaries, tiered stories, and dark mode support. The digest is split into readable chunks for Claude (MAX_TOKENS_PER_FILE = 10,000).

### 7-Day Deduplication
Headlines are stored in SQLite and checked against new articles. Stories only repeat if there's a major development (marked [UPDATE]).

### Docker-First for Automation
Everything runs in a single container for cron jobs. For interactive use, run locally with `python run.py`.

### Internet Check
Pipeline checks connectivity before running. If offline, exits cleanly.

### RSS Feed Retry
Flaky feeds are retried up to 3 times with exponential backoff (1s, 2s, 4s). Transient errors (timeouts, network issues) trigger retries; parse errors do not.

## Troubleshooting

### "No digest generated"
- Check `data/digest.log` for errors
- Ensure Claude Code CLI is authenticated: `claude --version`
- Try running manually: `python run.py --dry-run`

### Email not sending
- Verify SMTP credentials in `.env`
- For Gmail, ensure you're using an App Password
- Check `data/digest.log` for SMTP errors

### Stale/repeated stories
```bash
sqlite3 data/digest.db "SELECT * FROM shown_narratives ORDER BY shown_at DESC LIMIT 20;"
```

### Container issues
```bash
docker compose build --no-cache
docker info  # Verify Docker is running
```

## License

MIT
