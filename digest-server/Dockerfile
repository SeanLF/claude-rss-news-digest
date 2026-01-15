# Build stage
FROM rust:1.92-alpine AS builder

RUN apk add --no-cache musl-dev gcc

WORKDIR /app
COPY Cargo.toml Cargo.lock* ./
COPY src ./src

# Build for native musl target (Alpine is already musl-based)
RUN cargo build --release

# Runtime stage - Alpine for musl compatibility
FROM alpine:3.21

ARG OCI_TITLE="digest-server"
ARG OCI_DESCRIPTION="Minimal HTTP server for serving news digest HTML from SQLite"
ARG OCI_SOURCE="https://github.com/SeanLF/claude-rss-news-digest"
ARG OCI_LICENSES="MIT"

LABEL org.opencontainers.image.title="${OCI_TITLE}"
LABEL org.opencontainers.image.description="${OCI_DESCRIPTION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}"
LABEL org.opencontainers.image.licenses="${OCI_LICENSES}"

# Add non-root user
RUN addgroup -S app && adduser -S app -G app

COPY --from=builder /app/target/release/digest-server /usr/local/bin/

EXPOSE 8080

USER app:app

ENTRYPOINT ["digest-server"]
