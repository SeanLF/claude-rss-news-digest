FROM node:24-slim

ARG OCI_TITLE="claude-rss-news-digest"
ARG OCI_DESCRIPTION="Automated news digest: RSS feeds → Claude curation → HTML email"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Install CA certificates for SSL
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

# Install Python 3.14 + dependencies
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 \
    && ln -s /opt/python/*/bin/python3 /usr/local/bin/python3 \
    && ln -s /usr/local/bin/python3 /usr/local/bin/python \
    && uv pip install --python /usr/local/bin/python3 --break-system-packages feedparser resend premailer

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user for security
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Copy application
COPY run.py sources.json digest.css digest-template.html mcp_server.py .mcp.json ./
COPY .claude/commands/ /home/appuser/.claude/commands/

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app /home/appuser/.claude

# Switch to non-root user
USER appuser

# Default command (can be overridden)
CMD ["python3", "run.py"]
