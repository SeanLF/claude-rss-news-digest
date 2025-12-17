#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG_FILE="$(pwd)/digest.log"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOG_FILE"; }

# Check internet connectivity
check_internet() {
    curl -s --max-time 5 https://api.anthropic.com > /dev/null 2>&1
}

if ! check_internet; then
    log "No internet connection, skipping digest"
    exit 0
fi

# Load environment
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Check if OrbStack is running
ORBSTACK_WAS_RUNNING=true
if ! pgrep -q "OrbStack"; then
    ORBSTACK_WAS_RUNNING=false
    log "Starting OrbStack..."
    open -a OrbStack
    # Wait for Docker to be ready
    for i in {1..30}; do
        if docker info > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    if ! docker info > /dev/null 2>&1; then
        log "Failed to start OrbStack/Docker"
        exit 1
    fi
    log "OrbStack started"
fi

# Run Claude Code in Docker to generate digest
log "Running digest generation..."
docker compose run --rm news-digest

# Find the latest digest file
LATEST_DIGEST=$(ls -t output/digest-*.txt 2>/dev/null | head -1)

if [ -z "$LATEST_DIGEST" ]; then
    log "No digest generated"
    # Stop OrbStack if we started it
    if [ "$ORBSTACK_WAS_RUNNING" = false ]; then
        log "Stopping OrbStack..."
        osascript -e 'quit app "OrbStack"'
    fi
    exit 1
fi

# Send email
log "Sending digest: $LATEST_DIGEST"
python3 send_email.py "$LATEST_DIGEST"

log "Digest sent successfully"

# Stop OrbStack if we started it
if [ "$ORBSTACK_WAS_RUNNING" = false ]; then
    log "Stopping OrbStack..."
    osascript -e 'quit app "OrbStack"'
fi
