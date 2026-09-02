# News Digest commands
# Run `make` or `make help` to see available targets

.DEFAULT_GOAL := help
.PHONY: ci ci-fix ci-full test eval eval-stages eval-coherence eval-repair a11y lighthouse web-check deploy deploy-dry migrate migrate-status \
        ssh db-clone usage usage-daily analytics analytics-list analytics-q versions circulation preview anatomy prompt help

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
eval-repair: ## Harness-faithful REPAIR error-removal/preservation eval (MAKES model calls; opt-in)
	bin/eval-repair
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
