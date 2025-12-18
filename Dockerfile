FROM node:24-slim

# Install CA certificates for SSL
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

# Install Python 3.14 + dependencies
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 \
    && ln -s /opt/python/*/bin/python3 /usr/local/bin/python3 \
    && ln -s /usr/local/bin/python3 /usr/local/bin/python \
    && uv pip install --python /usr/local/bin/python3 --break-system-packages feedparser resend

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user for security
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Copy application
COPY run.py sources.json ./
COPY .claude/commands/ /home/appuser/.claude/commands/

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app /home/appuser/.claude

# Switch to non-root user
USER appuser

# Default command (can be overridden)
CMD ["python3", "run.py"]
