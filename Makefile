# Front door for the repo's tasks. Run `make` to list them.
#
# The dev-environment logic lives in use_mem0/up and use_mem0/down, not here:
# it needs process-group tracking and readiness polling, which read far better
# as shell than as make recipes. These targets delegate so that `make up` and
# `./use_mem0/up` are the same thing rather than two implementations.

APP := use_mem0

.DEFAULT_GOAL := help
.PHONY: help up down clean logs test build lint

help: ## List available targets
	@grep -hE '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) \
		| sed -e 's/:[^#]*## /|/' \
		| awk -F'|' '{ printf "  \033[1m%-8s\033[0m %s\n", $$1, $$2 }'

up: ## Start the chatbot: Postgres, backend, frontend
	@$(APP)/up

down: ## Stop it, keeping the database
	@$(APP)/down

clean: ## Stop it and drop the database volume
	@$(APP)/down --clean

logs: ## Follow the backend and frontend logs
	@tail -f $(APP)/.dev/*.log

test: ## Run the backend suite (needs a running Postgres: make up)
	@cd $(APP)/backend && UV_PROJECT_ENVIRONMENT="$$(../venv-path)" uv run pytest

build: ## Production build of the frontend
	@cd $(APP)/frontend && npm run build

lint: ## Lint the frontend
	@cd $(APP)/frontend && npm run lint
