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

# Recipients (comma-separated, each receives their own email)
DIGEST_EMAIL=you@example.com,friend@example.com

# Optional
DIGEST_NAME=News Digest
DIGEST_DOMAIN=news-digest.example.com  # For "View in browser" link
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

## Server Deployment

Production deployment uses Terraform (in seanfloyd.dev repo) to provision:

- **Systemd timer** - Runs daily at 07:00 UTC
- **Docker volume** - Persists SQLite database
- **Claude OAuth** - Uses Pro subscription credentials (refreshed automatically)
- **digest-server** - Serves web view at `news-digest.seanfloyd.dev` via Kamal proxy

### Build and Push Images

```bash
# From news-digest repo
docker buildx build --platform linux/amd64 -t seanfloyd-hetzner.tail739266.ts.net:5443/news-digest:latest --push .
docker buildx build --platform linux/amd64 -t seanfloyd-hetzner.tail739266.ts.net:5443/digest-server:latest --push ./digest-server
```

### Apply Terraform

```bash
cd /path/to/seanfloyd.dev/infrastructure/terraform
export HCLOUD_TOKEN=$(op read "op://Private/seanfloyd.dev/HETZNER_API_TOKEN")
export PORKBUN_API_KEY=$(op read "op://Private/seanfloyd.dev/PORKBUN_API_KEY")
export PORKBUN_SECRET_KEY=$(op read "op://Private/seanfloyd.dev/PORKBUN_SECRET_KEY")
export TF_VAR_news_digest_resend_api_key=$(op read "op://Private/seanfloyd.dev/NEWS_DIGEST_RESEND_API_KEY")
terraform apply
```

### Manual Operations

```bash
# Test run (no email)
ssh root@seanfloyd-hetzner 'systemctl start news-digest.service'
journalctl -fu news-digest

# Refresh Claude credentials (if expired)
docker compose run --rm news-digest claude --print "test"
scp data/.claude/.credentials.json root@seanfloyd-hetzner:/opt/news-digest/.claude/
ssh root@seanfloyd-hetzner 'chmod 644 /opt/news-digest/.claude/.credentials.json'
```

## License

MIT
