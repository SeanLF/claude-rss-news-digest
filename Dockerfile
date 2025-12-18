FROM python:3.12-slim

# Install Node.js (for Claude Code CLI)
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install Python dependencies
RUN pip install --no-cache-dir feedparser resend

# Create non-root user for security
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Copy application
COPY run.py sources.json ./
COPY .claude/ /home/appuser/.claude/

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app /home/appuser/.claude

# Switch to non-root user
USER appuser

# Default command (can be overridden)
CMD ["python", "run.py"]
