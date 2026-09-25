# News Digest commands
# Run `make` or `make help` to see available targets

.DEFAULT_GOAL := help
.PHONY: ci ci-fix a11y lighthouse web-check deploy ssh db-clone usage usage-daily analytics \
        analytics-list analytics-q help

# Default window for the analytics queries; override with RUNS=N
RUNS ?= 30

## CI
ci: ## Run all checks (in Docker)
	bin/ci
ci-fix: ## Auto-fix style issues
	bin/ci --fix

## Web gates
a11y: ## Fast structural a11y invariant check (no browser; suitable per-commit)
	bin/a11y-check
lighthouse: ## Lighthouse a11y/BP/SEO gate on the design mockups (pre-deploy; needs headless Chrome)
	bin/lighthouse
web-check: ## Both gates against the pages the site (digest-site) really serves (pre-deploy; use FAST=1 to skip Lighthouse)
	bin/web-check $(if $(FAST),--fast,)

## Deploy
deploy: ## Deploy HEAD's images (built by CI) with seanfloyd-infra's bin/deploy-digest (INFRA_DIR in .env)
	@if [ -f .env ]; then . ./.env; fi; \
	test -n "$$INFRA_DIR" || { echo "INFRA_DIR is unset: set it in .env to the seanfloyd-infra checkout" >&2; exit 2; }; \
	test -x "$$INFRA_DIR/bin/deploy-digest" || { echo "no executable $$INFRA_DIR/bin/deploy-digest" >&2; exit 2; }; \
	exec "$$INFRA_DIR/bin/deploy-digest" "$$(git rev-parse HEAD)"

## Database
db-clone: ## Clone production database locally
	bin/db-clone
usage: ## Token usage breakdown (requires db-clone)
	bin/usage
usage-daily: ## Daily usage totals (requires db-clone)
	bin/usage daily

## Analytics
analytics: ## Run every stored analytics query (usage: make analytics [RUNS=30])
	bin/analytics run --all --runs $(RUNS) --timing
analytics-list: ## List the stored analytics questions
	bin/analytics list
analytics-q: ## Run one analytics query (usage: make analytics-q Q=funnel-per-run [RUNS=30])
ifndef Q
	$(error Q is required. Usage: make analytics-q Q=funnel-per-run. See: make analytics-list)
endif
	bin/analytics run $(Q) --runs $(RUNS)

## Server
ssh: ## SSH to production server
	bin/ssh

## Help
help: ## Show this help
	@awk '/^## /{printf "\n\033[1m%s\033[0m\n", substr($$0,4)} \
		/^[a-zA-Z_-]+:.*?## /{split($$0,a,":.*?## "); printf "  \033[36m%-16s\033[0m %s\n", a[1], a[2]}' \
		$(MAKEFILE_LIST)

## Dev stack
# docker-compose.yml's dev stack: Temporal and the worker, the TypeScript site and resend-fake, over one product
# database (`digest` in digest-pg). COMPOSE picks the project, e.g. COMPOSE="docker compose -p mine".
COMPOSE ?= docker compose
DEV_SERVICES = digest-worker python-worker digest-site resend-fake temporal
# What dev-up rebuilds: the services built from the tree, digest-migrate included (up --build would rebuild it
# as a dependency). dev-import leaves resend-fake out: loading a database is no reason to restart the mail.
DEV_BUILD = digest-migrate digest-worker python-worker digest-site resend-fake
PG_NETWORK = $$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}}{{end}}' $$($(COMPOSE) ps -q digest-pg))

dev-up: ## Start the dev stack: pipeline on Temporal, site, resend-fake, one product database (keeps its data)
	docker volume create news-digest_claude-sessions >/dev/null  # the worker's Claude Code login, shared by every project; a no-op once it exists
	$(COMPOSE) build $(DEV_BUILD)
	$(COMPOSE) up -d --wait $(DEV_SERVICES)
	$(COMPOSE) exec -T digest-worker node dist/cli/set-current.js  # a versioned worker gets no runs until its build is current
	@$(MAKE) --no-print-directory dev-urls COMPOSE='$(COMPOSE)'

dev-urls: ## Where the dev stack answers (OrbStack domains)
	@p=$$($(COMPOSE) config 2>/dev/null | sed -n 's/^name: //p'); \
	echo "site          http://digest-site.$$p.orb.local:8080  (https://digest-site.$$p.orb.local, as mailed links spell it)"; \
	echo "resend-fake   http://resend-fake.$$p.orb.local:8025  (every email and broadcast; nothing is delivered)"; \
	echo "temporal UI   http://temporal.$$p.orb.local:8233"

dev-down: ## Stop the dev stack; keeps its volumes (the product database, Temporal's history, resend-fake's mail and contacts)
	$(COMPOSE) stop $(DEV_SERVICES) digest-pg

dev-import: ## Replace the dev stack's product database with a copy of the prod clone (make db-clone first), then start the stack; resend-fake is left running
	@$(COMPOSE) up -d --wait digest-pg && \
	$(COMPOSE) exec -T digest-pg psql -q -U postgres -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname = 'digest_clone'" | grep -q 1 \
	  || { echo "no digest_clone in the dev stack (make db-clone)"; exit 2; }; \
	$(COMPOSE) stop digest-site digest-worker >/dev/null 2>&1; \
	$(COMPOSE) exec -T digest-pg psql -qtA -U postgres -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'digest_clone'" >/dev/null && \
	$(COMPOSE) exec -T digest-pg psql -q -U postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS digest WITH (FORCE)" -c "CREATE DATABASE digest TEMPLATE digest_clone" && \
	$(MAKE) --no-print-directory dev-up COMPOSE='$(COMPOSE)' DEV_BUILD='$(filter-out resend-fake,$(DEV_BUILD))'

backfill-markdown: ## Fill each stored issue's Markdown from its HTML where the pipeline wrote none (the imported issues; a rerun is a no-op)
	$(COMPOSE) run --rm --no-deps digest-worker node dist/cli/backfill-markdown.js

dev-mail-clear: ## Empty resend-fake: caught mail and the dev audience's contacts, which otherwise survive restarts
	$(COMPOSE) exec -T resend-fake node -e "fetch('http://127.0.0.1:8025/api/reset', { method: 'POST', headers: { 'x-resend-fake-reset': 'yes' } }).then(async (r) => { console.log(await r.text()); process.exit(r.ok ? 0 : 1); }, (e) => { console.error(String(e)); process.exit(1); })"

# The issue is dated the UTC day a run starts, so a new run is today's: DATE defaults to it, and another
# day is refused unless it names the run to resume. ARGS=--force runs today again: a new revision of the
# issue on the site, never a second send.
digest-approve digest-reject: DATE ?= $(shell date -u +%Y-%m-%d)

digest-start: ## Start today's (UTC) DigestWorkflow on the dev stack and wait for it (ARGS=--force: run today again, published, never re-sent; DATE=2026-09-18 ARGS="--resume 300")
	$(COMPOSE) run --rm --no-deps digest-worker node dist/cli/start.js $(DATE) $(ARGS)

digest-approve: ## Send a held run now instead of at the hold's end (DATE defaults to today, UTC; a resumed run's is the DATE it was started with)
	$(COMPOSE) exec -T temporal temporal workflow signal --workflow-id digest-$(DATE) --name approve --input '{"decision":"approve"}'

digest-reject: ## Stop a held run: nothing is published or sent (DATE defaults to today, UTC)
	$(COMPOSE) exec -T temporal temporal workflow signal --workflow-id digest-$(DATE) --name approve --input '{"decision":"reject"}'

digest-schedule: ## Create or update the daily 10:25Z schedule on the dev stack's Temporal
	$(COMPOSE) run --rm --no-deps digest-worker node dist/cli/schedule.js

schema-types: ## Regenerate digest/src/store/schema.gen.ts, the product schema's row types, from the migrations on a fresh database in ci-pg (CI fails while it is stale)
	docker compose run --rm --build -T -v "$(CURDIR)/digest/src/store:/app/digest/src/store" ci-ts sh scripts/schema-types.sh

check-injections: ## Render every stored issue in the dev stack's database and list each date whose site chrome failed to inject (exit 1 on any; read-only)
	$(COMPOSE) run --rm --build --no-deps -e DIGEST_DATABASE_URL="postgres://digest_ro:digest_ro@digest-pg:5432/digest?sslmode=disable" digest-worker npm run --silent check-injections

band: ## Same-day curation band of the TypeScript workflow via promptfoo, on a copy of the dev stack's database (RUN=300 DATE=2026-09-18 REPS=3; model calls, ~$4/rep)
	@stamp=band_$$(date -u +%Y%m%dT%H%M%SZ | tr 'A-Z' 'a-z'); \
	$(COMPOSE) up -d --wait digest-pg && \
	$(COMPOSE) exec -T digest-pg psql -q -U postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE digest ALLOW_CONNECTIONS false" \
	  -c "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = 'digest'" -c "CREATE DATABASE $$stamp TEMPLATE digest" >/dev/null; made=$$?; \
	$(COMPOSE) exec -T digest-pg psql -q -U postgres -c "ALTER DATABASE digest ALLOW_CONNECTIONS true"; test $$made = 0 && \
	DIGEST_DB_NAME=$$stamp BROADCAST_ENABLED=false $(COMPOSE) up -d --build --wait digest-worker python-worker && \
	$(COMPOSE) exec -T digest-worker node dist/cli/set-current.js && \
	(cd digest && npm run build && BAND_DB=postgres://postgres:digest@127.0.0.1:$${DIGEST_PG_PORT:-5433}/$$stamp TEMPORAL_ADDRESS=127.0.0.1:$${TEMPORAL_PORT:-7233} npx --yes promptfoo@0.123.1 eval -c gate/band.yaml --repeat $${REPS:-3} -j 1 --no-cache -o ../data/$$stamp.json); status=$$?; \
	$(COMPOSE) up -d --force-recreate digest-worker >/dev/null; exit $$status  # the worker goes back to the digest database, broadcast on

judges: ## Two judge families x5 (REPS=5) on a gate fixture (FIXTURE=day-300, or e.g. day-305/python) via promptfoo, in the worker container (model calls, ~$5)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); fx=$${FIXTURE:-day-300}; test -f docs/proposed/gate-fixtures/$$fx/digest.html || { echo "no fixture docs/proposed/gate-fixtures/$$fx/{digest.html,inputs/}"; exit 2; }; \
	sed "s#gate-fixtures/day-300/#gate-fixtures/$$fx/#g" digest/gate/judges.yaml > digest/gate/judges.run.yaml; \
	$(JUDGE_RUN) sh -c "mkdir -p /tmp/codex && cp /run/codex-auth.json /tmp/codex/auth.json && $(CODEX_INSTALL) && npx --yes promptfoo@0.123.1 eval -c gate/judges.run.yaml --repeat $${REPS:-5} -j 1 --no-cache -o ../data/judges-$$(echo $$fx | tr / -)-$$stamp.json && node dist/cli/agreement.js ../data/judges-$$(echo $$fx | tr / -)-$$stamp.json"

planted: ## COHERENCE planted-defect band on the new runner via promptfoo, in the worker container (REPS=3; ~$1/rep)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); $(DIGEST_RUN) npx --yes promptfoo@0.123.1 eval -c gate/planted.yaml --repeat $${REPS:-3} -j 1 --no-cache -o ../data/planted-$$stamp.json

fulltext-fork: ## Fulltext fork: every extractor arm over a saved corpus via promptfoo, on the host (DIR=data/fulltext-fork-<stamp>)
	@test -n "$(DIR)" || { echo "DIR=data/fulltext-fork-<stamp> is required"; exit 2; }
	cd digest && npm run build --silent && FULLTEXT_FORK_DIR="$(CURDIR)/$(DIR)" npx --yes promptfoo@0.123.1 eval -c gate/fulltext.yaml -j 4 --no-cache -o "$(CURDIR)/$(DIR)/results.json"

# Evals that make model calls run in the worker image, as production calls do: the Claude Code binary
# the SDK spawns refuses to run nested inside a Claude Code session, and the image is the pinned one.
# The Codex judge signs in with a copy of the host's Codex login in a writable CODEX_HOME (the login
# is mounted read-only; Codex writes beside it). Without a login the SDK hangs rather than failing.
# PROMPTFOO_EVAL_TIMEOUT_MS bounds each judgement, so a stuck judge fails its test.
# The Codex CLI the judge runs, installed per run at a pinned version with its platform binary named
# explicitly: npx's own resolution sometimes drops that optional dependency (npm/cli#4828), and the
# judge then fails with "Unable to locate Codex CLI binaries". judges.yaml points codex_path_override here.
CODEX_VERSION = 0.156.1
CODEX_INSTALL = arch=\$$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/') && npm i --silent --prefix /tmp/cx @openai/codex@$(CODEX_VERSION) @openai/codex-linux-\$$arch@npm:@openai/codex@$(CODEX_VERSION)-linux-\$$arch && test -x /tmp/cx/node_modules/.bin/codex
JUDGE_RUN = $(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/docs:/app/docs:ro" -v "$(HOME)/.codex/auth.json:/run/codex-auth.json:ro" -e CODEX_HOME=/tmp/codex -e PROMPTFOO_EVAL_TIMEOUT_MS=1200000 digest-judge
DIGEST_RUN = $(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/docs:/app/docs:ro" digest-worker
