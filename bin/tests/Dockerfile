# syntax=docker/dockerfile:1
# CI for the operator scripts in bin/ (docker-compose.yml ci-scripts): pytest over bin/tests and ruff.
# The repo is bind-mounted at /app, so the image holds only the tools. git and jq are here because
# bin/deploy's rollback and SBOM tests skip without them; make for test_make_deploy.py. Base images
# pinned like digest/python's.
FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2
RUN apt-get update && apt-get install -y --no-install-recommends git jq make && rm -rf /var/lib/apt/lists/*
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --break-system-packages pytest==9.1.1 pytest-xdist==3.8.0 ruff==0.16.9
WORKDIR /app
