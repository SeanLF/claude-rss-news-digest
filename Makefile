# News Digest commands
# Run `make` or `make help` to see available targets

.DEFAULT_GOAL := help
.PHONY: ci ci-fix ci-full test deploy deploy-dry migrate migrate-status \
        ssh db-clone usage usage-daily versions circulation prompt help

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
