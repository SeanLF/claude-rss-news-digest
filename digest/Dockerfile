FROM node:26-slim
# 1001 is the uid that owns news-digest-data and news-digest-claude in production (the newsroom
# image's appuser). node:26-slim already holds 1000, so a bare useradd lands on 1001 today only by
# accident; a different uid cannot write digest.db.
RUN useradd -u 1001 -m appuser
WORKDIR /app/digest
COPY digest/package.json digest/package-lock.json ./
RUN npm ci
COPY digest/ ./
# The feed catalogue fetch reads (newsroom owns it until the Python tree retires).
COPY newsroom/sources.json /app/sources.json
# The render's template and stylesheet, and the design tokens the email's colours come from.
COPY newsroom/templates/digest-template.html newsroom/templates/digest.css /app/newsroom/templates/
COPY design/tokens.css /app/design/tokens.css
# Build with the dev toolchain, then drop it: tsc is a devDependency.
RUN npm run build && npm prune --omit=dev
# Below the build: these change every commit. GIT_SHA is what digest_runs.git_sha records;
# the revision label is what bin/deploy's provenance check reads.
ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA}
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
USER appuser
CMD ["node", "dist/worker.js"]
