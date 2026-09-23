# syntax=docker/dockerfile:1
# The Python worker: the activity that stays in Python (fulltext), on the `python` task queue. Build context is
# the repo root, so the newsroom modules come from newsroom/src and exist in one place.
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/
WORKDIR /app
# The repo tracks no uv.lock (.gitignore); the pins live in pyproject.toml, as in newsroom/.
COPY digest/python/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv uv venv .venv --python python3.14 \
    && uv pip install --python .venv -r pyproject.toml --group dev

FROM python:3.14-slim
RUN useradd --create-home appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY newsroom/src/fulltext.py newsroom/src/config.py digest/python/worker.py digest/python/test_worker.py /app/src/
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONUNBUFFERED=1
# Changes every commit, so it sits below the venv copy; bin/deploy's provenance check reads it.
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
USER appuser
WORKDIR /app/src
CMD ["python", "worker.py"]
