# syntax=docker/dockerfile:1
# The Python worker: the activity that stays in Python (fulltext), on the `python` task queue. A standalone
# package: nothing here is read from newsroom/.
#
# Base images by digest, so a rebuild is the image that was tested. Bump them together with
# `docker buildx imagetools inspect python:3.14-slim` and `... ghcr.io/astral-sh/uv:<version>`.
FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS base
RUN useradd --create-home appuser
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONUNBUFFERED=1

# The dependencies as uv.lock pins them, the whole tree; --frozen fails the build on a lock that no
# longer matches pyproject.toml instead of re-resolving.
FROM base AS deps
COPY --from=uv /uv /usr/local/bin/
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY digest/python/pyproject.toml digest/python/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# CI's target (docker-compose.yml ci-python): the shipped venv plus the dev group, and the tests.
FROM deps AS test-deps
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

FROM base AS test
COPY --from=test-deps --chown=appuser:appuser /app/.venv /app/.venv
COPY digest/python/fulltext.py digest/python/settings.py digest/python/worker.py /app/src/
COPY digest/python/tests /app/tests/
USER appuser
WORKDIR /app/src

# The image that ships: last, so a build with no --target (CI's images.yml) gets it.
FROM base
COPY --from=deps --chown=appuser:appuser /app/.venv /app/.venv
COPY digest/python/fulltext.py digest/python/settings.py digest/python/worker.py /app/src/
# Changes every commit, so it sits below the venv copy; CI's attestation covers the image; this names the commit.
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
USER appuser
WORKDIR /app/src
# Healthy while the worker polls (worker.py touches the file each 5 s while running), so Kamal counts
# a new worker deployed only then. No network call: find prints the file only when under a minute old.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s \
  CMD test -n "$(find /tmp/worker-alive -mmin -1)"
CMD ["python", "worker.py"]
