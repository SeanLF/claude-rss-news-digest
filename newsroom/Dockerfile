FROM alpine:3.23

ARG OCI_TITLE="digest-newsroom"
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
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/

# Install Python 3.14 and symlink to /usr/local/bin
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 \
    && ln -sf /opt/python/*/bin/python3 /usr/local/bin/python3

# Create non-root user with fixed UID/GID for volume compatibility
RUN addgroup -g 1001 appuser && adduser -u 1001 -G appuser -s /bin/bash -D appuser

# Install Claude Code CLI (native method) as appuser
ENV USE_BUILTIN_RIPGREP=0
USER appuser
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root

WORKDIR /app

# Install dependencies from pyproject.toml (before copying app code for better caching)
COPY newsroom/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv .venv && uv pip install --python .venv -r pyproject.toml

# Track which code version produced each digest
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}

# Copy application code
COPY newsroom/src/ ./src/
COPY newsroom/sources.json ./
COPY newsroom/templates/ ./templates/
COPY newsroom/.mcp.json ./

# Copy shared resources from repo root
COPY migrations/ ./migrations/
COPY bin/ ./bin/
COPY .claude/commands/ /app/.claude/commands/

# Create data directory and set permissions
RUN mkdir -p /app/data /home/appuser/.bash_history_dir \
    && ln -s /home/appuser/.local/bin/claude /usr/local/bin/claude \
    && chown -R appuser:appuser /app /home/appuser/.bash_history_dir
ENV HISTFILE=/home/appuser/.bash_history_dir/.bash_history

# Switch to non-root user
USER appuser

# Default command (can be overridden)
CMD [".venv/bin/python", "src/run.py"]
