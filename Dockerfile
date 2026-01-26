FROM alpine:3.23

ARG OCI_TITLE="claude-rss-news-digest"
ARG OCI_DESCRIPTION="Automated news digest: RSS feeds → Claude curation → HTML email"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Install dependencies for Claude Code on Alpine + CA certs
RUN apk add --no-cache ca-certificates libgcc libstdc++ ripgrep bash curl jq

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

# Install Python 3.14 and symlink to /usr/local/bin
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 \
    && ln -s /opt/python/*/bin/python3 /usr/local/bin/python3

# Create non-root user with fixed UID/GID for volume compatibility
RUN addgroup -g 1001 appuser && adduser -u 1001 -G appuser -s /bin/bash -D appuser

# Install Claude Code CLI (native method) as appuser
ENV USE_BUILTIN_RIPGREP=0
USER appuser
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root

WORKDIR /app

# Install dependencies from pyproject.toml (before copying app code for better caching)
COPY pyproject.toml ./
RUN uv venv .venv && uv pip install --python .venv -r pyproject.toml

# Copy application and create data directory
COPY run.py sources.json digest.css digest-template.html mcp_server.py .mcp.json ./
COPY .claude/commands/ /home/appuser/.claude/commands/
RUN mkdir -p /app/data \
    && ln -s /home/appuser/.local/bin/claude /usr/local/bin/claude \
    && chown -R appuser:appuser /app /home/appuser/.claude

# Switch to non-root user
USER appuser

# Default command (can be overridden)
CMD [".venv/bin/python", "run.py"]
