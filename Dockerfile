FROM python:3.12-slim

# Install Node.js (for Claude Code CLI)
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install Python dependencies
RUN pip install --no-cache-dir feedparser

WORKDIR /app

# Copy application
COPY run.py sources.json ./
COPY claude-config/ /root/.claude/

# Create data directory
RUN mkdir -p /app/data

ENTRYPOINT ["python", "run.py"]
