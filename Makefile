# News Digest commands
# Run `make` or `make help` to see available targets

.DEFAULT_GOAL := help
.PHONY: ci ci-fix ci-full test eval eval-stages eval-coherence eval-repair eval-select-order replay digest a11y lighthouse web-check deploy deploy-dry migrate migrate-status \
        ssh db-clone usage usage-daily analytics analytics-list analytics-q versions circulation preview anatomy prompt ask-eval help

# Default window for the analytics queries; override with RUNS=N
RUNS ?= 30

## CI
ci: ## Run all checks (Python + Rust, in Docker)
	bin/ci
ci-fix: ## Auto-fix style issues
	bin/ci --fix
ci-full: ## Full CI including cargo audit
	bin/ci --full

## Test
test: ## Run Python tests only (in Docker)
	docker compose run --rm --build ci pytest -v newsroom/tests/
eval: ## Run the offline eval-floor regression gate (no model calls)
	bin/eval-regression
eval-stages: ## Grade each subagent's recorded output (per-stage L1, no model calls)
	bin/eval-stages
eval-coherence: ## Harness-faithful COHERENCE recall/false-drop eval (MAKES model calls; opt-in)
	bin/eval-coherence
ask-eval: ## Behavioural eval for /ask (MAKES model calls; seeds a planted-injection archive)
	bin/ask-eval
eval-repair: ## Harness-faithful REPAIR error-removal/preservation eval (MAKES model calls; opt-in)
	bin/eval-repair
eval-select-order: ## SELECT order-dependence harness on one archived run (MAKES model calls; usage: make eval-select-order RUN=298)
	bin/eval-select-order fetch $(RUN) && bin/eval-select-order run $(RUN) --reps 5 --arms fixed,shuffled,sorted
replay: ## Replay a finished run's render tail from its archived artifacts (no model calls; usage: make replay RUN=285)
	bin/replay $(RUN)
# --build, because `docker compose run digest-newsroom` on its own runs whatever the image was
# last built from: on 2026-09-17 that was a day-old tree, and it reported a working feature as
# broken. The pipeline keeps src BAKED (no mount) so a local run stays prod-faithful; --build
# only makes the bake current.
digest: ## Run the pipeline locally against the CURRENT tree (usage: make digest ARGS="--dry-run")
	docker compose run --rm --build digest-newsroom $(ARGS)
a11y: ## Fast structural a11y invariant check (no browser; suitable per-commit)
	bin/a11y-check
lighthouse: ## Lighthouse a11y/BP/SEO gate on the design mockups (pre-deploy; needs headless Chrome)
	bin/lighthouse
web-check: ## Both gates against the pages circulation really serves (pre-deploy; use FAST=1 to skip Lighthouse)
	bin/web-check $(if $(FAST),--fast,)

## Deploy
deploy: ## Deploy to production (build, push, terraform, migrate)
	bin/deploy
deploy-dry: ## Preview deployment without changes
	bin/deploy --dry-run

## Database
migrate: ## Apply pending database migrations
	bin/migrate
migrate-status: ## Show migration status
	bin/migrate --status
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

## Development
circulation: ## Run circulation server locally (fast Rust rebuilds)
	bin/circulation

preview: ## Render + screenshot the digest locally, no Docker (usage: make preview [FIXTURE=path])
	bin/render-preview $(FIXTURE)

anatomy: ## Regenerate the pipeline anatomy page + README diagram (usage: make anatomy [RUN=284] [DB=path])
	docker compose run --rm --build --entrypoint python3 ci newsroom/tools/pipeline_anatomy.py \
		--html docs/pipeline-anatomy.html --svg-dir docs --readme README.md \
		--code-version $(shell git rev-parse --short HEAD) \
		$(if $(RUN),--run $(RUN),) $(if $(DB),--db $(DB),)

## Checks
versions: ## Check for dependency updates
	bin/check-versions

## Prompts
prompt: ## Run prompt experiment (usage: make prompt NAME=baseline)
ifndef NAME
	$(error NAME is required. Usage: make prompt NAME=baseline)
endif
	bin/test-prompt run $(NAME)

## Help
help: ## Show this help
	@awk '/^## /{printf "\n\033[1m%s\033[0m\n", substr($$0,4)} \
		/^[a-zA-Z_-]+:.*?## /{split($$0,a,":.*?## "); printf "  \033[36m%-16s\033[0m %s\n", a[1], a[2]}' \
		$(MAKEFILE_LIST)

temporal-up: ## Local Temporal dev server 1.32.0 + UI (127.0.0.1:8233) + the digest worker
	docker volume create news-digest_claude-sessions >/dev/null  # the login volume the newsroom stack owns; a no-op once it exists
	docker compose --env-file .env -f digest/compose.temporal.yml up -d --build
	docker compose --env-file .env -f digest/compose.temporal.yml exec -T digest-worker node dist/cli/set-current.js  # a versioned worker gets no runs until its build is current

temporal-down: ## Stop local Temporal; keeps its SQLite volume
	docker compose --env-file .env -f digest/compose.temporal.yml down

digest-start: ## Start one DigestWorkflow on local Temporal and wait for it (usage: make digest-start DATE=2026-09-21 [ARGS="--resume 300 --force"])
	docker compose --env-file .env -f digest/compose.temporal.yml run --rm digest-worker node dist/cli/start.js $(DATE) $(ARGS)

digest-schedule: ## Create or update the daily 10:25Z schedule on local Temporal
	docker compose --env-file .env -f digest/compose.temporal.yml run --rm digest-worker node dist/cli/schedule.js

import-check: ## Import a copy of the prod clone into a fresh Postgres and hold it to the design's §5.1 and prepare's parity (SRC=data/prod-20260923b.db; host-only, ~1 min)
	@src=$${SRC:-data/prod-20260923b.db}; copy=data/import-check.db; db=import_check; \
	test -r "$$src" || { echo "no $$src (make db-clone)"; exit 2; }; \
	rm -f "$$copy" && cp -c "$$src" "$$copy" && \
	docker compose up -d --wait digest-pg && \
	docker compose exec -T digest-pg psql -q -U postgres -c "DROP DATABASE IF EXISTS $$db" -c "CREATE DATABASE $$db" && \
	IMPORT_NETWORK=$$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}}{{end}}' $$(docker compose ps -q digest-pg)) \
	  bin/import-legacy "$$copy" "postgres://postgres:digest@digest-pg:5432/$$db?sslmode=disable" && \
	docker compose run --rm --build -e IMPORTED_CLONE_URL="postgres://postgres:digest@digest-pg:5432/$$db?sslmode=disable" \
	  -e PARITY_DATABASE_URL="postgres://postgres:digest@digest-pg:5432/$$db?sslmode=disable" \
	  ci-ts npx vitest run src/store/import.clone.test.ts src/prepare/prepare.parity.test.ts; status=$$?; rm -f "$$copy"; exit $$status

band: ## Same-day curation band of the TypeScript workflow via promptfoo (RUN=300 DATE=2026-09-18 REPS=3; model calls, ~$4/rep)
	@stamp=band_$$(date -u +%Y%m%dT%H%M%SZ | tr 'A-Z' 'a-z'); \
	docker compose --env-file .env -f digest/compose.temporal.yml up -d --wait digest-db && \
	docker compose --env-file .env -f digest/compose.temporal.yml exec -T digest-db psql -q -U postgres -c "CREATE DATABASE $$stamp TEMPLATE digest" && \
	DIGEST_DB_NAME=$$stamp docker compose --env-file .env -f digest/compose.temporal.yml up -d --build && \
	docker compose --env-file .env -f digest/compose.temporal.yml exec -T digest-worker node dist/cli/set-current.js && \
	(cd digest && npm run build && BAND_DB=postgres://postgres:digest@127.0.0.1:5433/$$stamp npx --yes promptfoo@0.123.1 eval -c gate/band.yaml --repeat $${REPS:-3} -j 1 --no-cache -o ../data/$$stamp.json); status=$$?; \
	docker compose --env-file .env -f digest/compose.temporal.yml up -d --force-recreate digest-worker >/dev/null; exit $$status  # the worker goes back to the digest database

judges: ## Two judge families x5 (REPS=5) on a gate fixture (FIXTURE=day-300, or e.g. day-305/python) via promptfoo, in the worker container (model calls, ~$5)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); fx=$${FIXTURE:-day-300}; test -f docs/proposed/gate-fixtures/$$fx/digest.html || { echo "no fixture docs/proposed/gate-fixtures/$$fx/{digest.html,inputs/}"; exit 2; }; \
	sed "s#gate-fixtures/day-300/#gate-fixtures/$$fx/#g" digest/gate/judges.yaml > digest/gate/judges.run.yaml; \
	$(JUDGE_RUN) sh -c "mkdir -p /tmp/codex && cp /run/codex-auth.json /tmp/codex/auth.json && $(CODEX_INSTALL) && npx --yes promptfoo@0.123.1 eval -c gate/judges.run.yaml --repeat $${REPS:-5} -j 1 --no-cache -o ../data/judges-$$(echo $$fx | tr / -)-$$stamp.json && node dist/cli/agreement.js ../data/judges-$$(echo $$fx | tr / -)-$$stamp.json"

planted: ## COHERENCE planted-defect band on the new runner via promptfoo, in the worker container (REPS=3; ~$1/rep)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); $(DIGEST_RUN) npx --yes promptfoo@0.123.1 eval -c gate/planted.yaml --repeat $${REPS:-3} -j 1 --no-cache -o ../data/planted-$$stamp.json

fulltext-pages: ## Fulltext fork corpus: fetch production's candidates for runs >= 300 once, with trafilatura (DB=data/digest.db)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); docker compose run --rm --build -v "$(CURDIR)/newsroom/src:/app/src:ro" -v "$(CURDIR)/newsroom/tools:/app/tools:ro" -e PYTHONPATH=/app/src --entrypoint /app/.venv/bin/python3 digest-newsroom /app/tools/fulltext_fork_pages.py /app/$${DB:-data/digest.db} /app/data/fulltext-fork-$$stamp

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
JUDGE_RUN = docker compose --env-file .env -f digest/compose.temporal.yml run --rm --build --no-deps -v "$(CURDIR)/docs:/app/docs:ro" -v "$(HOME)/.codex/auth.json:/run/codex-auth.json:ro" -e CODEX_HOME=/tmp/codex -e PROMPTFOO_EVAL_TIMEOUT_MS=1200000 digest-judge
DIGEST_RUN = docker compose --env-file .env -f digest/compose.temporal.yml run --rm --build --no-deps -v "$(CURDIR)/docs:/app/docs:ro" digest-worker
