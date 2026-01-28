# News Digest

Automated daily news digest powered by Claude. Fetches from diverse RSS sources, deduplicates against recent history, clusters into narratives, and emails a curated HTML summary via Resend.

## How It Works

1. **Fetch** - Python script pulls RSS feeds, filters by last run time
2. **Prepare** - TF-IDF pre-filters duplicates, splits articles into CSV files for Claude
3. **Curate** - Claude reads articles, filters noise, selects stories into tiers
4. **Render** - Python renders HTML from Claude's JSON selections
5. **Email** - Sends via [Resend Broadcasts](https://resend.com/broadcasts) to audience subscribers
6. **Record** - Stores shown headlines in SQLite for 7-day deduplication window

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

**Option 1: Interactive login (local development)**
```bash
docker compose run --rm news-digest claude login
```
This persists your auth in a Docker volume (`claude-config`).

**Option 2: OAuth token (production/CI)**
```bash
# Generate a 1-year token
claude setup-token

# Add to .env
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

### Run

```bash
# Full run: fetch, generate, email, record
./run-digest.sh

# Dry run (no email, no DB record)
./run-digest.sh --dry-run

# Preview latest digest in browser
./run-digest.sh --preview

# Validate all RSS feeds
./run-digest.sh --validate

# Test Resend config
./run-digest.sh --test-email
```

### Web Viewer (Optional)

The `digest-server` serves past digests via HTTP for "View in browser" links:

```bash
# Start both services
docker compose up -d

# Or just the web server
docker compose up -d digest-server
```

Access at `http://localhost:8080/YYYY-MM-DD` (e.g., `/2026-01-15`).

### Scheduling

**Local (cron):**
```bash
# Daily at 07:00 UTC
0 7 * * * /path/to/news-digest/run-digest.sh >> /path/to/news-digest/data/cron.log 2>&1
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
- Ensure Claude is authenticated: `docker compose run --rm news-digest claude --version`
- Try: `./run-digest.sh --dry-run`

### Email not sending
- Verify Resend API key in `.env`
- Check that RESEND_FROM is a verified domain or use `onboarding@resend.dev` for testing
- Check `data/digest.log` for errors

### Container issues
```bash
docker compose build --no-cache
```

### Claude says MCP tool isn't available
The MCP server needs access to dependencies in the venv. Check `.mcp.json` uses `.venv/bin/python`:
```json
{
  "mcpServers": {
    "news-digest": {
      "command": ".venv/bin/python",
      "args": ["mcp_server.py"]
    }
  }
}
```
Using `python3` instead will fail because venv deps aren't available to global Python.

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
