#!/bin/bash
set -e

cd "$(dirname "$0")"

# Load environment
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Note: ANTHROPIC_API_KEY is optional for Docker if auth is configured differently
# For local runs, you can use `claude login` with Pro subscription instead

# Pass all args to container
docker compose run --rm news-digest python run.py "$@"
