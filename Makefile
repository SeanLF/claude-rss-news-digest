# News Digest commands
# Run `make` or `make help` to see available targets

.DEFAULT_GOAL := help
.PHONY: ci ci-fix ci-full test eval eval-stages eval-coherence eval-repair a11y lighthouse deploy deploy-dry migrate migrate-status \
        ssh db-clone usage usage-daily versions circulation preview prompt help

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

## Server
ssh: ## SSH to production server
	bin/ssh

## Development
circulation: ## Run circulation server locally (fast Rust rebuilds)
	bin/circulation

preview: ## Render + screenshot the digest locally, no Docker (usage: make preview [FIXTURE=path])
	bin/render-preview $(FIXTURE)

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
