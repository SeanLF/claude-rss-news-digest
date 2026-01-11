# News Digest

Automated daily news digest powered by Claude. Fetches from diverse RSS sources, deduplicates against recent history, clusters into narratives, and emails a curated HTML summary via Resend.

## How It Works

1. **Fetch** - Python script pulls RSS feeds, filters by last run time
2. **Prepare** - Splits articles into CSV files (~10k tokens each) for Claude to read
3. **Curate** - Claude reads all articles, deduplicates, filters noise, clusters stories
4. **Generate** - Outputs HTML digest with tiered stories and regional clusters
5. **Email** - Sends via [Resend](https://resend.com) to configured recipients
6. **Record** - Stores shown headlines in SQLite for 7-day deduplication

## Prerequisites

- Docker
- [Resend](https://resend.com) API key (free tier: 3,000 emails/month)

## Setup

```bash
# Clone the repo
git clone https://github.com/yourusername/news-digest.git
cd news-digest

# Create .env with your config
cp .env.example .env
# Edit .env with your RESEND_API_KEY, RESEND_FROM, DIGEST_EMAIL
```

### Configuration (.env)

```bash
# Resend settings (https://resend.com/api-keys)
RESEND_API_KEY=re_xxxxxxxx_xxxxxxxxxxxxxxxxxxxx
RESEND_FROM=onboarding@resend.dev  # Or your verified domain

# Recipients (comma-separated, first is To:, rest are BCC)
DIGEST_EMAIL=you@example.com,friend@example.com

# Optional display name
DIGEST_NAME=News Digest
```

### Authenticate Claude (one-time)

Before first run, log in to Claude inside the container:

```bash
docker compose run --rm news-digest claude login
```

This persists your auth in a Docker volume (`claude-config`).

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

### Cron Setup

Add to crontab (`crontab -e`):

```bash
# Daily at 07:00 UTC
0 7 * * * /path/to/news-digest/run-digest.sh >> /path/to/news-digest/data/cron.log 2>&1
```

## Output Format

The digest is an HTML email with:

- **Regional Summary** - Quick overview by region (Americas, Europe, Asia-Pacific, ME&Africa, Tech)
- **Must Know (3-4)** - Stories you'd be embarrassed not to know, with "Why it matters"
- **Should Know (5-8)** - Important but not urgent
- **Quick Signals (10-15)** - One-liners worth tracking
- **Below the Fold** - Regional clusters for remaining stories

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

## License

MIT
