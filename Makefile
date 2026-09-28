# SPDX-License-Identifier: AGPL-3.0-only
# Every target runs through Docker. Host prerequisites: Docker (Compose v2), make, git.

SHELL := /bin/sh
.DEFAULT_GOAL := help

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE_FILES := --project-directory . -f infra/compose/compose.yml
DEV  := docker compose --env-file .env $(COMPOSE_FILES) -f infra/compose/compose.dev.yml
TEST := docker compose --env-file .env $(COMPOSE_FILES) -f infra/compose/compose.test.yml
TOOLS_IMAGE := pickwise-tools:local
# Plain `docker run`, so it works before .env has secrets (compose would refuse to parse).
TOOLS_RUN := docker run --rm -u $(HOST_UID):$(HOST_GID) -v "$(CURDIR)":/repo -w /repo $(TOOLS_IMAGE)
TOOLS := $(DEV) run --rm --no-deps tools
UV := uv run --project apps/backend --frozen
RUFF_CFG := --config apps/backend/pyproject.toml

WAIT_TIMEOUT ?= 600
# When `up` fails, a one-shot service usually says why; show its last lines.
SHOW_ONESHOT_LOGS = echo "--- last log lines of the setup containers ---" >&2; \
	$(DEV) logs --no-log-prefix --tail 5 migrate s3-init seed-demo >&2
define PRINT_URLS
	@printf '\n  web       http://localhost:3000\n  API docs  http://localhost:8000/docs\n  Mailpit   http://localhost:8025\n\n'
endef

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-12s %s\n",$$1,$$2}'

# --- environment ------------------------------------------------------------
.env:
	cp .env.example .env

.PHONY: tools-image
tools-image:
	docker build -q -t $(TOOLS_IMAGE) -f infra/docker/tools.Dockerfile . >/dev/null

.PHONY: env
env: .env tools-image ## Create .env and generate any missing dev secrets
	$(TOOLS_RUN) sh infra/scripts/gen-secrets.sh .env

# --- dev stack --------------------------------------------------------------
.PHONY: dev
dev: env ## Build and start the whole stack (stub scanner), wait until healthy
	SCANNER=stub $(DEV) up -d --build --remove-orphans || { $(SHOW_ONESHOT_LOGS); exit 1; }
	sh infra/scripts/wait-healthy.sh $(WAIT_TIMEOUT) $(DEV)
	@echo "Pickwise is up:"
	$(PRINT_URLS)

.PHONY: dev-full
dev-full: env ## Same as dev, plus real ClamAV (SCANNER=clamav)
	SCANNER=clamav $(DEV) --profile full up -d --build --remove-orphans || { $(SHOW_ONESHOT_LOGS); exit 1; }
	sh infra/scripts/wait-healthy.sh 1200 $(DEV) --profile full
	@echo "Pickwise is up (real ClamAV):"
	$(PRINT_URLS)

.PHONY: down
down: ## Stop the stack (keeps data)
	$(DEV) --profile full --profile tools down --remove-orphans

.PHONY: reset
reset: ## Stop the stack and delete all its data volumes
	$(DEV) --profile full --profile tools down --volumes --remove-orphans

.PHONY: logs
logs: ## Follow logs: make logs s=api
	$(DEV) logs -f $(s)

.PHONY: shell
shell: ## Shell in a running service: make shell s=api
	$(DEV) exec $(or $(s),api) sh

.PHONY: psql
psql: ## psql as the postgres superuser
	$(DEV) exec postgres psql -U postgres -d pickwise

.PHONY: s3-ls
s3-ls: ## List every object in the dev object storage
	$(TOOLS) sh -c 'AWS_ACCESS_KEY_ID="$$S3_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$$S3_SECRET_ACCESS_KEY" AWS_DEFAULT_REGION="$$S3_REGION" \
		sh -c "for b in \$$(aws --endpoint-url http://s3:8333 s3 ls | cut -c21-); do echo \"s3://\$$b\"; aws --endpoint-url http://s3:8333 s3 ls --recursive s3://\$$b; done"'

# --- database ---------------------------------------------------------------
.PHONY: migrate
migrate: env ## Run migrations (and seeds) against the dev database
	$(DEV) run --rm migrate

.PHONY: migration
migration: tools-image ## New Alembic revision: make migration m="add foo"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	$(TOOLS_RUN) sh -c 'uv sync --project apps/backend --frozen -q && cd apps/backend && uv run --frozen alembic revision -m "$(m)"'

.PHONY: seed
seed: env ## Seed reference data (permission catalog, statutory rule sets)
	$(DEV) run --rm migrate pickwise db seed

.PHONY: seed-demo
seed-demo: env ## Create the demo tenants (dev only)
	$(DEV) run --rm seed-demo

# --- quality ----------------------------------------------------------------
.PHONY: deps
deps: tools-image
	$(TOOLS_RUN) sh -c 'uv sync --project apps/backend --frozen -q && pnpm install --frozen-lockfile --silent'

.PHONY: lint
lint: deps ## ruff, import-linter, SPDX headers, eslint, prettier
	$(TOOLS_RUN) sh -c '$(UV) ruff check $(RUFF_CFG) apps/backend db && $(UV) ruff format --check $(RUFF_CFG) apps/backend db \
		&& (cd apps/backend && uv run --frozen lint-imports) \
		&& sh infra/scripts/check-spdx.sh \
		&& pnpm -r --workspace-concurrency=1 lint && pnpm format:check'

.PHONY: typecheck
typecheck: deps ## mypy --strict and tsc
	$(TOOLS_RUN) sh -c 'cd apps/backend && uv run --frozen mypy && cd ../.. && pnpm -r typecheck'

.PHONY: test
test: env ## Backend + database tests in a disposable stack
	$(TEST) --profile test build backend-test
	$(TEST) --profile test run --rm backend-test; status=$$?; \
		$(TEST) --profile test down --volumes --remove-orphans >/dev/null 2>&1; exit $$status

.PHONY: test-db
test-db: env ## Database-level tests only (db/tests)
	$(TEST) --profile test build backend-test
	$(TEST) --profile test run --rm backend-test pytest ../../db/tests; status=$$?; \
		$(TEST) --profile test down --volumes --remove-orphans >/dev/null 2>&1; exit $$status

.PHONY: e2e
e2e: ## Playwright against the running dev stack (run make dev first)
	docker run --rm --network pickwise_default -u $(HOST_UID):$(HOST_GID) \
		-e HOME=/tmp -e CI=$(CI) -e E2E_BASE_URL=http://web:3000 -e pnpm_config_store_dir=/repo/.cache/pnpm-store \
		-v "$(CURDIR)":/repo -w /repo \
		mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27 \
		sh -c 'npx --yes pnpm@12.6.0 --filter @pickwise/web exec playwright test'

.PHONY: web-build
web-build: deps ## Production build of the web app
	$(TOOLS_RUN) pnpm --filter @pickwise/web build

.PHONY: hooks
hooks: tools-image ## Install git hooks that run pre-commit inside the tools container
	@mkdir -p .git/hooks
	@printf '#!/bin/sh\n# Installed by make hooks: runs pre-commit in the tools container.\nexec docker run --rm -u %s:%s -v "$$(git rev-parse --show-toplevel)":/repo -w /repo %s pre-commit run --hook-stage pre-commit\n' \
		"$(HOST_UID)" "$(HOST_GID)" "$(TOOLS_IMAGE)" > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit"

# --- codegen ----------------------------------------------------------------
.PHONY: openapi
openapi: deps ## Regenerate packages/api-client from the backend OpenAPI schema
	$(TOOLS_RUN) sh -c '$(UV) pickwise openapi export --out packages/api-client/openapi.json \
		&& pnpm --filter @pickwise/api-client generate && pnpm exec prettier --write packages/api-client/src/schema.d.ts >/dev/null'

.PHONY: openapi-check
openapi-check: openapi ## Fail if the generated client is out of date
	@git diff --exit-code -- packages/api-client || (echo "api-client is stale: run make openapi and commit" && exit 1)

.PHONY: docs-pii
docs-pii: ## Regenerate the PII section of DATA_MODEL.md (arrives in Phase 1)
	@echo "docs-pii: db/pii_classification.yaml arrives in Phase 1; nothing to generate yet"

# --- scanning ---------------------------------------------------------------
.PHONY: scan-check
scan-check: env ## Stream EICAR through the scanner adapter to real clamd; must be infected
	SCANNER=clamav $(DEV) --profile full up -d --wait --wait-timeout 1200 clamav
	$(DEV) --profile full run --rm --no-deps -e SCANNER=clamav api pickwise scan-check

# --- Jev eval (Phase 11) ----------------------------------------------------
.PHONY: eval eval-live
eval: ## Screening eval with FakeJevClient (arrives in Phase 11)
	@echo "eval: the Jev screening eval harness arrives in Phase 11"
eval-live: ## Opt-in eval against the live Jev API (arrives in Phase 11)
	@echo "eval-live: arrives in Phase 11; it is opt-in and never runs in CI"
