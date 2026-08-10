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
    && apt-get install -y --no-install-recommends ca-certificates ripgrep bash \
    # perl-base is a Debian "essential" package but NOTHING installed depends on it (apt -s purge
    # cascades to nothing) and the pipeline never invokes perl (python + the SDK-bundled CLI + bash).
    # It only ships CVEs (perl-Archive-Tar / IO-Compress, fix_deferred). Force-remove it -- done as
    # the last apt step so no later dpkg maintainer script could need it.
    && dpkg --purge --force-remove-essential perl-base \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with fixed UID/GID for volume compatibility.
RUN groupadd -g 1001 appuser \
    && useradd -u 1001 -g appuser -s /bin/bash -m -d /home/appuser appuser

# The Claude Code CLI is NOT installed separately: claude-agent-sdk bundles it (see the pinned SDK
# in constraints-prod.txt), and the pipeline drives that bundle. We used to also `curl | bash` the
# standalone CLI -- a second ~236MB binary + a floating supply-chain dep, and the SDK never used it
# (_find_cli returns the bundle first). `claude` on PATH is symlinked to the bundle below instead,
# so interactive `claude` == the pinned pipeline CLI. USE_BUILTIN_RIPGREP=0 -> use the apt ripgrep.
ENV USE_BUILTIN_RIPGREP=0

WORKDIR /app

# Prebuilt dependency venv (same base image + same /app/.venv path -> relocatable). Copied
# already-owned by appuser so no post-copy `chown -R` duplicates the venv layer.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Track which code version produced each digest.
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}

# Keep below the venv copy: this ARG changes every commit, and moving it up with the other
# org.opencontainers.image.* labels would re-run the apt install and re-copy the ~500MB venv.
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"

# Application code + shared resources (owned by appuser directly, no recursive chown).
COPY --chown=appuser:appuser newsroom/src/ ./src/
COPY --chown=appuser:appuser newsroom/sources.json ./
COPY --chown=appuser:appuser newsroom/templates/ ./templates/
# Canonical design tokens (repo-root design/). Build context is the repo root
# (docker-compose sets `context: .`), so the source path is design/. Needed at
# runtime: render prepends design/tokens.css to digest.css for var() resolution.
COPY --chown=appuser:appuser design/ ./design/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser bin/ ./bin/
# bin/analytics reads these .sql files at runtime, so shipping the script without
# them puts a broken tool on the server. Worth carrying (60K, stdlib-only, opens the
# DB read-only and writes only to stdout) because the production database lives in a
# Docker volume and the host has no sqlite3 binary -- this image is the only way to
# query it in place. The alternative, bin/db-clone, raw-cats 117MB over SSH and can
# truncate, and a clone is point-in-time when the live DB is what you need.
COPY --chown=appuser:appuser analytics/ ./analytics/
COPY --chown=appuser:appuser .claude/agents/ /app/.claude/agents/
COPY --chmod=755 newsroom/entrypoint.sh /app/entrypoint.sh

# Runtime dirs + claude symlink -> the SDK-bundled CLI (located via the package, so the path is
# robust to the python minor version). chown only the two new dirs, not the whole tree.
RUN mkdir -p /app/data /home/appuser/.bash_history_dir \
    && ln -s "$(/app/.venv/bin/python -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))')" /usr/local/bin/claude \
    && test -x /usr/local/bin/claude \
    && chown appuser:appuser /app/data /home/appuser/.bash_history_dir
ENV HISTFILE=/home/appuser/.bash_history_dir/.bash_history

USER appuser

# Entrypoint handles flag passthrough (--dry-run) vs arbitrary commands (claude --version)
ENTRYPOINT ["/app/entrypoint.sh"]
