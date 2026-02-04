# News Digest

Automated daily news digest powered by Claude. Fetches from diverse RSS sources, deduplicates against recent history, clusters into narratives, and emails a curated HTML summary via Resend.

## How It Works

1. **Fetch** - Python script pulls RSS feeds, filters by last run time
2. **Prepare** - TF-IDF pre-filters duplicates, splits articles into CSV files for Claude
3. **Curate** - Claude reads articles, filters noise, selects stories into tiers
4. **Render** - Python renders HTML from Claude's JSON selections
5. **Email** - Sends via [Resend Broadcasts](https://resend.com/broadcasts) to audience subscribers
6. **Record** - Stores shown headlines in SQLite for 7-day deduplication window

## Repository Structure

```
news-digest/
├── newsroom/           # Python pipeline
│   ├── src/            # Modules: run, feeds, prepare, claude, digest, render, broadcast, db
│   ├── templates/      # HTML template and CSS
│   ├── tests/          # Python tests
│   └── sources.json    # RSS feed definitions
├── circulation/        # Rust web server for archive viewing
├── data/               # Runtime data (database, logs)
├── migrations/         # SQLite schema migrations
└── bin/                # CLI scripts (ci, migrate, test-prompt, deploy)
```

## Prerequisites

- Docker
- [Resend](https://resend.com) API key (free tier: 3,000 emails/month or unlimited broadcasts to up to 1,000 contacts)

## Setup

```bash
# Clone the repo
git clone https://github.com/yourusername/news-digest.git
cd news-digest

# Create .env with your config
cp .env.example .env
# Edit .env with your Resend settings
```

### Configuration (.env)

```bash
# Resend settings (https://resend.com/api-keys)
RESEND_API_KEY=re_xxxxxxxx_xxxxxxxxxxxxxxxxxxxx
RESEND_FROM=onboarding@resend.dev  # Or your verified domain

# Resend Audience ID for broadcasts (https://resend.com/audiences)
# Create an audience and add contacts to manage recipients
RESEND_AUDIENCE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Optional - Digest metadata
DIGEST_NAME=News Digest
DIGEST_DOMAIN=news-digest.example.com  # For "View in browser" link
SOURCE_URL=https://github.com/you/news-digest  # Footer link to source code
MODEL_NAME=Claude (Opus 4.5)  # AI model name in footer
ARCHIVE_URL=https://news-digest.example.com  # "Past digests" link
AUTHOR_NAME=Your Name  # Footer attribution
AUTHOR_URL=https://yoursite.com  # Author link
```

### Authenticate Claude

**Option 1: OAuth token** (recommended, valid 1 year):
```bash
claude setup-token
# Add to .env: CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

**Option 2: API key** (from [console.anthropic.com](https://console.anthropic.com/)):
```bash
# Add to .env: ANTHROPIC_API_KEY=sk-ant-...
```

### Run

```bash
# Full run: fetch, generate, email, record
docker compose run --rm digest-newsroom

# Dry run (no email, no DB record)
docker compose run --rm digest-newsroom python src/run.py --dry-run

# Preview latest digest in browser
docker compose run --rm digest-newsroom python src/run.py --preview

# Validate all RSS feeds
docker compose run --rm digest-newsroom python src/run.py --validate

# Test Resend config
docker compose run --rm digest-newsroom python src/run.py --test-email
```

### Web Viewer (Optional)

The `circulation` server serves past digests via HTTP for "View in browser" links:

```bash
# Start both services
docker compose up -d

# Or just the web server
docker compose up -d digest-circulation
```

Access at `http://localhost:8080/YYYY-MM-DD` (e.g., `/2026-01-15`).

With [OrbStack](https://orbstack.dev), set `ORBSTACK_DOMAIN=news-digest.yourdomain.local` in `.env` for automatic local DNS (e.g., `http://news-digest.yourdomain.local/2026-01-15`).

Stats dashboard at `/stats` shows source health, usage metrics, and dedup effectiveness.

### Scheduling

**Local (cron):**
```bash
# Daily at 07:00 UTC
0 7 * * * cd /path/to/news-digest && docker compose run --rm digest-newsroom >> data/cron.log 2>&1
```

**Server (systemd):** See deployment section below.

## Output Format

The digest is an HTML email with:

- **Regional Summary** - Quick overview by region (Americas, Europe, Asia-Pacific, ME&Africa, Tech)
- **Must Know (3+)** - Stories you'd be embarrassed not to know, with "Why it matters"
- **Should Know (5+)** - Important but not urgent
- **Also Notable** - One-liners clustered by region

Supports dark mode automatically.

## Sources

| Category | Sources | Bias |
|----------|---------|------|
| Wire | Reuters | center |
| UK | BBC World, Guardian | center/center-left |
| US | NPR World, NYT, WaPo | center-left |
| Canadian | Globe and Mail, CBC | center |
| Finance | FT, WSJ | center-right |
| Economist | International, Asia, Europe, Americas, Middle East & Africa | center-right |
| Tech | HN, Ars Technica, The Verge, Rest of World | center |
| Asia-Pacific | SCMP (3), Nikkei Asia, Straits Times, Rappler | center |
| Middle East | Al Jazeera | center |
| Europe | Le Monde, Der Spiegel, Deutsche Welle | center/center-left |
| India | The Hindu | center |
| Africa | Daily Maverick | center-left |
| Investigative | ProPublica, The Intercept | center-left/left |

## Troubleshooting

### "No digest generated"
- Check `data/digest.log` for errors
- Ensure Claude is authenticated: `docker compose run --rm digest-newsroom claude --version`
- Try: `docker compose run --rm digest-newsroom python src/run.py --dry-run`

### Email not sending
- Verify Resend API key in `.env`
- Check that RESEND_FROM is a verified domain or use `onboarding@resend.dev` for testing
- Check `data/digest.log` for errors

### Container issues
```bash
docker compose build --no-cache
```

### Claude says MCP tool isn't available
The MCP server needs access to dependencies in the venv. Check `newsroom/.mcp.json` uses `.venv/bin/python`:
```json
{
  "mcpServers": {
    "news-digest": {
      "command": ".venv/bin/python",
      "args": ["src/mcp_server.py"]
    }
  }
}
```
Using `python3` instead will fail because venv deps aren't available to global Python.

## Database

SQLite database at `data/digest.db`. Migrations managed via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/).

```bash
# Apply pending migrations (runs in Docker)
bin/migrate

# Check migration status
bin/migrate --status

# Preview without applying
bin/migrate --dry-run
```

On first run, `bin/migrate` creates the database and applies the baseline schema.

Production migrations run automatically during `bin/deploy`.

## Development

```bash
# Install git hooks
brew install lefthook
lefthook install

# Run all checks in Docker (lint, types, security, tests)
bin/ci

# Auto-fix style issues
bin/ci --fix
```

The `bin/ci` script runs checks in Docker for reproducibility:
- **ruff** - linting and formatting
- **mypy** - type checking
- **bandit** - security scanning
- **pytest** - tests

Use `bin/ci --local` to skip Docker (requires local dev dependencies).

Git hooks run `bin/ci` on pre-commit.

## Server Deployment

For production deployment (systemd timers, Docker images, Terraform), see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## License

MIT
