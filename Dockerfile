FROM python:3.12-slim

# Install Node.js (for Claude Code CLI) and dependencies
RUN apt-get update && apt-get install -y \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install Python dependencies
RUN pip install --no-cache-dir feedparser

WORKDIR /app

# Copy application files
COPY fetch_feeds.py send_email.py init_db.py ./
COPY claude-config/ /root/.claude/

# Create data directory
RUN mkdir -p /app/data

# Entry point
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
