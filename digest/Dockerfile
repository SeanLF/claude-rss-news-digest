# One Dockerfile, three images (docs/2026-09-24-web-tier-and-ops-decisions.md): `site`, `dev`, and
# `worker`, which stays the last stage so a build without --target is still the worker.
FROM node:26-slim AS base
# 1001 is the uid that owns news-digest-data and news-digest-claude in production (the newsroom
# image's appuser). node:26-slim already holds 1000, so a bare useradd lands on 1001 today only by
# accident; a different uid cannot write digest.db.
RUN useradd -u 1001 -m appuser
WORKDIR /app/digest

FROM base AS build
COPY digest/package.json digest/package-lock.json ./
RUN npm ci
COPY digest/ ./
# tsc for the worker, esbuild for the site, the site's SBOM from the lockfile and the bundle's
# metafile (an image with no node_modules gives a scanner nothing to read), then drop the dev toolchain.
RUN npm run build && npm run bundle:site && npm run sbom:site && npm prune --omit=dev

# The web tier: the bundle, its assets and the two files it reads, and no node_modules.
FROM base AS site
COPY --from=build /app/digest/bundle/dist ./dist
COPY --from=build /app/digest/bundle/site.cdx.json /app/digest/bundle/meta.json ./
# Read relative to the bundle (site/assets.ts): dist/site/main.js -> ../../assets/site.
COPY digest/assets/site ./assets/site
COPY newsroom/sources.json /app/sources.json
COPY design/tokens.css /app/design/tokens.css
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
USER appuser
CMD ["node", "dist/site/main.js"]

FROM base AS app
COPY --from=build /app/digest ./
# The feed catalogue fetch reads (newsroom owns it until the Python tree retires).
COPY newsroom/sources.json /app/sources.json
# The render's template and stylesheet, and the design tokens the email's colours come from.
COPY newsroom/templates/digest-template.html newsroom/templates/digest.css /app/newsroom/templates/
COPY design/tokens.css /app/design/tokens.css
# Below the build: these change every commit. GIT_SHA is what runs.git_sha records;
# the revision label is what bin/deploy's provenance check reads.
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
USER appuser
CMD ["node", "dist/worker.js"]

# Local only: the Codex judge is a native binary, and the slim image has no system CA store for it
# (Node and the Bun-built Claude CLI carry their own).
FROM app AS dev
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
USER appuser

FROM app AS worker
