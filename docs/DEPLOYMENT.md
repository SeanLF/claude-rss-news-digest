# Server Deployment

This document covers deploying news-digest to a production server. The main [README](../README.md) covers local development and Docker usage.

## Architecture

- **Systemd timer** - Runs daily at configured time (e.g., 07:00 UTC)
- **Docker containers** - digest-newsroom (cron job) + digest-circulation (web archive)
- **SQLite database** - Persisted in Docker volume
- **Claude OAuth** - Uses Max or Pro subscription credentials (refreshed automatically)
- **digest-circulation** - Optional web server for "View in browser" links

## Terraform Variables

If using Terraform for provisioning, add these to your variables:

```hcl
variable "news_digest_resend_api_key" {
  description = "Resend API key for sending emails"
  sensitive   = true
}

variable "news_digest_claude_oauth_token" {
  description = "Claude Code OAuth token (from setup-token, valid 1 year)"
  sensitive   = true
}

variable "news_digest_resend_audience_id" {
  description = "Resend Audience ID for broadcast recipients"
}

variable "news_digest_homepage_url" {
  description = "Homepage URL for footer link"
  default     = ""
}

variable "news_digest_source_url" {
  description = "Source code URL for footer link"
  default     = ""
}

variable "news_digest_archive_url" {
  description = "URL to past digests archive"
  default     = ""
}

variable "news_digest_author_name" {
  description = "Author name for footer attribution"
  default     = ""
}

variable "news_digest_author_url" {
  description = "Author URL for footer attribution"
  default     = ""
}

```

## Docker Images

Build and push images to your registry:

```bash
# digest-newsroom (main pipeline)
docker buildx build --platform linux/amd64 -t YOUR_REGISTRY/digest-newsroom:latest --push . -f newsroom/Dockerfile

# digest-circulation (web archive -- context is repo root, needs sources.json)
docker buildx build --platform linux/amd64 -t YOUR_REGISTRY/digest-circulation:latest --push -f circulation/Dockerfile .
```

## Systemd Service

Example systemd unit for running the digest:

```ini
[Unit]
Description=News Digest
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker compose -f /opt/news-digest/docker-compose.yml run --rm digest-newsroom
WorkingDirectory=/opt/news-digest

[Install]
WantedBy=multi-user.target
```

## Systemd Timer

```ini
[Unit]
Description=Run News Digest daily

[Timer]
OnCalendar=*-*-* 07:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

## Claude Authentication

The digest uses a Claude Max or Pro subscription via OAuth token. Generate a long-lived token (1 year validity):

```bash
claude setup-token
```

Add the token to your environment file on the server:

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

The token is passed via environment variable - no credentials file needed.

## digest-circulation Container

Environment variables for the web archive server:

| Variable | Description |
|----------|-------------|
| `DATABASE_PATH` | Path to SQLite database (default: `/data/digest.db`) |
| `PORT` | HTTP port (default: `8080`) |
| `DIGEST_NAME` | Display name for the site |
| `HOMEPAGE_URL` | Optional footer link to homepage |
| `SOURCE_URL` | Optional footer link to source code |
| `RESEND_API_KEY` | Optional, enables subscription form |
| `RESEND_AUDIENCE_ID` | Required if RESEND_API_KEY is set |

## Manual Operations

```bash
# Test run (no email)
ssh user@server 'systemctl start news-digest.service'
journalctl -fu news-digest

# Check timer status
ssh user@server 'systemctl list-timers news-digest.timer'

# View recent digests (no sqlite3 on server -- use the newsroom container)
ssh user@server 'docker compose -f /opt/news-digest/docker-compose.yml run --rm digest-newsroom .venv/bin/python -c "
import sqlite3; [print(r[0]) for r in sqlite3.connect(\"/app/data/digest.db\").execute(\"SELECT date FROM digests ORDER BY date DESC LIMIT 5\")]
"'
```
