# Build stage
FROM rust:1.94-alpine AS builder

RUN apk add --no-cache musl-dev gcc

WORKDIR /app

# Cache dependencies: only rebuilds when Cargo.toml/Cargo.lock change
COPY circulation/Cargo.toml circulation/Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/main.rs \
    && cargo build --release \
    && rm -rf src

# Build actual source (deps layer cached above)
COPY circulation/apple-touch-icon.png ./
COPY circulation/src ./src
COPY newsroom/sources.json ./sources.json
RUN touch src/main.rs && cargo build --release

# Runtime stage - Alpine for musl compatibility
FROM alpine:3.23

ARG OCI_TITLE="digest-circulation"
ARG OCI_DESCRIPTION="Minimal HTTP server for serving news digest HTML from SQLite"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Add non-root user
RUN addgroup -S app && adduser -S app -G app

COPY --from=builder /app/target/release/digest-circulation /usr/local/bin/

EXPOSE 8080

USER app:app

ENTRYPOINT ["digest-circulation"]
