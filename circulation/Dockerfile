# Build stage
FROM rust:1.96-alpine AS builder

RUN apk add --no-cache musl-dev gcc

WORKDIR /app

# Cache dependencies: only rebuilds when Cargo.toml/Cargo.lock change
COPY circulation/Cargo.toml circulation/Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/main.rs \
    && cargo build --release \
    && rm -rf src

# Build actual source (deps layer cached above)
COPY circulation/apple-touch-icon.png circulation/og-image.png ./
COPY circulation/src ./src
COPY newsroom/sources.json ./sources.json
# WORKDIR /app maps to circulation/, so repo-root maps to /. Mirror the local layout so the
# include_str!/include_bytes! relative paths (../../design, ../assets) resolve identically:
#   /app/src/assets.rs -> ../../design/tokens.css == /design/tokens.css
#   /app/src/assets.rs -> ../assets/fonts/*.woff2 == /app/assets/fonts/*.woff2
COPY design /design
COPY circulation/assets ./assets
RUN touch src/main.rs && cargo build --release

# Runtime stage - Alpine for musl compatibility
FROM alpine:3.24

ARG OCI_TITLE="digest-circulation"
ARG OCI_DESCRIPTION="Minimal HTTP server for serving news digest HTML from SQLite"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Add non-root user
# Fixed uid/gid 1001 to MATCH the newsroom image's appuser: both containers now
# write the shared news-digest-data volume (digest.db is 1001:1001, mode 644),
# and SQLite needs file+directory write to create its journal. The old -S
# (system) user got uid 100 and could only read -- /feedback 500'd with
# "attempt to write a readonly database" on an rw mount.
RUN addgroup -g 1001 app && adduser -u 1001 -G app -D -H app

COPY --from=builder /app/target/release/digest-circulation /usr/local/bin/

# Keep below the binary copy: this ARG changes every commit, and moving it up with the other
# org.opencontainers.image.* labels would redo the adduser layer and the copy on every deploy.
ARG OCI_REVISION=""
LABEL org.opencontainers.image.revision="${OCI_REVISION}"

EXPOSE 8080

USER app:app

ENTRYPOINT ["digest-circulation"]
