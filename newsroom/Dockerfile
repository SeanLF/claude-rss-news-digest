# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------- #
# Builder: create the dependency venv. Needs uv; installs sklearn/scipy/numpy
# from manylinux wheels (glibc base, no compiler needed). None of uv, the apt
# lists, or the uv cache reach the runtime image.
# ---------------------------------------------------------------------------- #
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

WORKDIR /app
# constraints-prod.txt pins the Agent SDK (and any other volatile dep) so a fresh PROD build is
# reproducible and a bad upstream release can't break it. CI (Dockerfile.ci) omits it and floats
# on purpose, so a bad release fails CI first. See newsroom/tests/test_sdk_pin.py.
COPY newsroom/pyproject.toml newsroom/constraints-prod.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv .venv --python python3.14 \
    && uv pip install --python .venv -r pyproject.toml -c constraints-prod.txt

# ---------------------------------------------------------------------------- #
# Runtime: slim Debian (glibc). Debian is required over Alpine because the
# scientific-Python deps have no musllinux wheels for Python 3.14. The venv is
# copied from the builder already owned by appuser (a single layer -- a
# `chown -R /app` after copying would duplicate the ~500MB venv into another).
# ---------------------------------------------------------------------------- #
FROM python:3.14-slim

ARG OCI_TITLE="digest-newsroom"
ARG OCI_DESCRIPTION="Automated news digest: RSS feeds → Claude curation → HTML email"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ripgrep bash curl jq \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with fixed UID/GID for volume compatibility.
RUN groupadd -g 1001 appuser \
    && useradd -u 1001 -g appuser -s /bin/bash -m -d /home/appuser appuser

# Install Claude Code CLI (native method) as appuser.
ENV USE_BUILTIN_RIPGREP=0
USER appuser
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root

WORKDIR /app

# Prebuilt dependency venv (same base image + same /app/.venv path -> relocatable). Copied
# already-owned by appuser so no post-copy `chown -R` duplicates the venv layer.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Track which code version produced each digest.
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}

# Application code + shared resources (owned by appuser directly, no recursive chown).
COPY --chown=appuser:appuser newsroom/src/ ./src/
COPY --chown=appuser:appuser newsroom/sources.json ./
COPY --chown=appuser:appuser newsroom/templates/ ./templates/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser bin/ ./bin/
COPY --chown=appuser:appuser .claude/agents/ /app/.claude/agents/
COPY --chmod=755 newsroom/entrypoint.sh /app/entrypoint.sh

# Runtime dirs + claude symlink (chown only the two new dirs, not the whole tree).
RUN mkdir -p /app/data /home/appuser/.bash_history_dir \
    && ln -s /home/appuser/.local/bin/claude /usr/local/bin/claude \
    && chown appuser:appuser /app/data /home/appuser/.bash_history_dir
ENV HISTFILE=/home/appuser/.bash_history_dir/.bash_history

USER appuser

# Entrypoint handles flag passthrough (--dry-run) vs arbitrary commands (claude --version)
ENTRYPOINT ["/app/entrypoint.sh"]
