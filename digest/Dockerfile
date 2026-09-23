FROM node:26-slim
# The slim image has no system CA store: Node carries its own, but native binaries (the Codex judge) do not.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m appuser
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
USER appuser
CMD ["node", "dist/worker.js"]
