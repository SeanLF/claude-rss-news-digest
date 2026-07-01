FROM python:3.14-slim

ARG OCI_TITLE="digest-newsroom"
ARG OCI_DESCRIPTION="Automated news digest: RSS feeds → Claude curation → HTML email"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Debian slim (glibc) base -- scientific-Python deps (scikit-learn/scipy/numpy) install from
# prebuilt manylinux wheels here; Alpine/musl forced a source build that has no wheels for
# Python 3.14 and no compiler in the slim image. Python 3.14 ships in the base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ripgrep bash curl jq \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

# Create non-root user with fixed UID/GID for volume compatibility
RUN groupadd -g 1001 appuser \
    && useradd -u 1001 -g appuser -s /bin/bash -m -d /home/appuser appuser

# Install Claude Code CLI (native method) as appuser
ENV USE_BUILTIN_RIPGREP=0
USER appuser
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root

WORKDIR /app

# Install dependencies from pyproject.toml (before copying app code for better caching).
# --python targets the base image's Python 3.14; wheels install with no compiler.
COPY newsroom/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv .venv --python python3.14 && uv pip install --python .venv -r pyproject.toml

# Track which code version produced each digest
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}

# Copy application code
COPY newsroom/src/ ./src/
COPY newsroom/sources.json ./
COPY newsroom/templates/ ./templates/

# Copy shared resources from repo root
COPY migrations/ ./migrations/
COPY bin/ ./bin/
COPY .claude/agents/ /app/.claude/agents/
COPY --chmod=755 newsroom/entrypoint.sh /app/entrypoint.sh

# Create data directory and set permissions
RUN mkdir -p /app/data /home/appuser/.bash_history_dir \
    && ln -s /home/appuser/.local/bin/claude /usr/local/bin/claude \
    && chown -R appuser:appuser /app /home/appuser/.bash_history_dir
ENV HISTFILE=/home/appuser/.bash_history_dir/.bash_history

# Switch to non-root user
USER appuser

# Entrypoint handles flag passthrough (--dry-run) vs arbitrary commands (claude --version)
ENTRYPOINT ["/app/entrypoint.sh"]
