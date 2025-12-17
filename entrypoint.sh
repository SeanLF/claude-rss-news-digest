#!/bin/bash
set -e

cd /app

LOG_FILE="/app/data/digest.log"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" | tee -a "$LOG_FILE"; }

# Check internet connectivity
if ! curl -s --max-time 10 https://api.anthropic.com > /dev/null 2>&1; then
    log "No internet connection, skipping digest"
    exit 0
fi

# Initialize database if needed
if [ ! -f /app/data/digest.db ]; then
    log "Initializing database..."
    python3 init_db.py
fi

# Fetch RSS feeds
log "Fetching RSS feeds..."
python3 fetch_feeds.py

# Run Claude Code to generate digest
log "Generating digest with Claude..."
claude --print -p /news-digest

# Find the latest digest file
LATEST_DIGEST=$(ls -t /app/data/output/digest-*.txt 2>/dev/null | head -1)

if [ -z "$LATEST_DIGEST" ]; then
    log "ERROR: No digest generated"
    exit 1
fi

# Send email
log "Sending digest: $LATEST_DIGEST"
python3 send_email.py "$LATEST_DIGEST"

log "Digest sent successfully"
