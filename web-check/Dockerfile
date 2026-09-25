# syntax=docker/dockerfile:1
# The pre-deploy web gate's tools (bin/web-check): pa11y-ci and Lighthouse CI on Debian's Chromium,
# so neither downloads its own browser.
FROM node:26-slim
RUN apt-get update && apt-get install -y --no-install-recommends chromium && rm -rf /var/lib/apt/lists/*
ENV PUPPETEER_SKIP_DOWNLOAD=1 CHROME_PATH=/usr/bin/chromium
WORKDIR /web-check
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY lighthouserc.json .pa11yci.json ./
ENV PATH=/web-check/node_modules/.bin:$PATH
