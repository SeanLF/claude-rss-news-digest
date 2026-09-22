FROM node:26-slim
RUN useradd -m appuser
WORKDIR /app/digest
COPY digest/package.json digest/package-lock.json ./
RUN npm ci
COPY digest/ ./
# Build with the dev toolchain, then drop it: tsc is a devDependency.
RUN npm run build && npm prune --omit=dev
USER appuser
CMD ["node", "dist/worker.js"]
