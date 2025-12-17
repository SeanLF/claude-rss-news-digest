#!/bin/bash
set -e

cd "$(dirname "$0")"

# Load environment
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Check required env vars
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY not set in .env"
    exit 1
fi

# Run the digest container
docker compose run --rm news-digest
