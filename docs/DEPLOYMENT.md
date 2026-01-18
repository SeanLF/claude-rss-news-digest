# Server Deployment

This document covers deploying news-digest to a production server. The main [README](../README.md) covers local development and Docker usage.

## Architecture

- **Systemd timer** - Runs daily at configured time (e.g., 07:00 UTC)
- **Docker containers** - news-digest (cron job) + digest-server (web archive)
- **SQLite database** - Persisted in Docker volume
- **Claude OAuth** - Uses Pro subscription credentials (refreshed automatically)
- **digest-server** - Optional web server for "View in browser" links

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

variable "news_digest_model_name" {
  description = "AI model name shown in footer (e.g., 'Claude (Opus 4.5)')"
  default     = "Claude"
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

variable "news_digest_css_url" {
  description = "External CSS URL for digest-server styling"
  default     = ""
}
```

## Docker Images

Build and push images to your registry:

```bash
# news-digest (main pipeline)
docker buildx build --platform linux/amd64 -t YOUR_REGISTRY/news-digest:latest --push .

# digest-server (web archive)
docker buildx build --platform linux/amd64 -t YOUR_REGISTRY/digest-server:latest --push ./digest-server
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
ExecStart=/usr/bin/docker compose -f /opt/news-digest/docker-compose.yml run --rm news-digest
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

The digest uses Claude Pro subscription via OAuth token. Generate a long-lived token (1 year validity):

```bash
claude setup-token
```

Add the token to your environment file on the server:

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

The token is passed via environment variable - no credentials file needed.

## digest-server Container

Environment variables for the web archive server:

| Variable | Description |
|----------|-------------|
| `DATABASE_PATH` | Path to SQLite database (default: `/data/digest.db`) |
| `PORT` | HTTP port (default: `8080`) |
| `DIGEST_NAME` | Display name for the site |
| `CSS_URL` | Optional external CSS URL |
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

# View recent digests
ssh user@server 'sqlite3 /opt/news-digest/data/digest.db "SELECT date FROM digests ORDER BY date DESC LIMIT 5"'
```
