# JALANI developer entry points. `make help` lists every target.
SHELL := /bin/bash
.DEFAULT_GOAL := help

SIM_URL ?= http://localhost:8000
SIM_COMPOSE := docker compose -f docker-compose.sim.yml
COMPOSE := docker compose
PY := uv run --no-project python
SCRIPT_PY := uv run --no-project --with httpx python

# Build metadata baked into every image and shown on /version.
export GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
export BUILD_TIME ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

# `make up-lite`: the product without the observability stack.
LITE_SERVICES := simulator postgres redis ingestor intelligence core-api web

.PHONY: help up up-lite down logs ps test lint fmt e2e loadtest demo rollback \
	sim-reset sim-run sim-pause sim-step sim-up sim-down sim-probe sim-experiments

help: ## List targets
	@grep -hE '^[a-zA-Z_.-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.env: ## Create .env from .env.example with random secrets (never overwrites)
	@$(PY) scripts/make_env.py

# --- stack ---------------------------------------------------------------------------------

up: .env ## Build and start the full stack; waits until every container is healthy
	$(COMPOSE) up -d --build --wait

up-lite: .env ## Build and start without observability
	$(COMPOSE) up -d --build --wait $(LITE_SERVICES)

down: ## Stop the stack (keeps volumes; `docker compose down -v` wipes data)
	$(COMPOSE) down

logs: ## Follow logs (S=service to filter)
	$(COMPOSE) logs -f --tail=100 $(S)

ps: ## Container status and health
	$(COMPOSE) ps

demo: .env ## Full stack with the simulator slowed to DEMO_SPEED, reset and running
	$(COMPOSE) -f docker-compose.yml -f docker-compose.override.demo.yml up -d --build --wait
	@$(MAKE) --no-print-directory sim-reset sim-run

rollback: ## Restart our services on an earlier local image tag: make rollback VERSION=v0.5.0
	@test -n "$(VERSION)" || { echo "usage: make rollback VERSION=<image tag>" >&2; exit 2; }
	IMAGE_TAG=$(VERSION) $(COMPOSE) up -d --no-build --wait ingestor intelligence core-api web

# --- quality -------------------------------------------------------------------------------

test: ## Python tests
	uv run pytest

lint: ## Everything CI checks: pre-commit on all files (ruff, mypy, eslint, prettier, gitleaks)
	uv run pre-commit run --all-files

fmt: ## Auto-format Python and web code
	uv run ruff check --fix services scripts
	uv run ruff format services scripts
	pnpm --dir web format

e2e: ## Playwright end-to-end tests (Phase 10)
	@echo "e2e: no Playwright suite yet (Phase 10)" >&2; exit 1

loadtest: ## k6 load tests against core-api (Phase 9)
	@echo "loadtest: no k6 scripts yet (Phase 9)" >&2; exit 1

# --- simulator control (admin API; bypasses faults) ----------------------------------------

sim-reset: ## Hard-reset the simulator world to tick 0 (leaves it PAUSED)
	@curl -fsS -X POST $(SIM_URL)/admin/reset && echo

sim-run: ## Start ticking
	@curl -fsS -X POST $(SIM_URL)/admin/run && echo

sim-pause: ## Stop ticking
	@curl -fsS -X POST $(SIM_URL)/admin/pause && echo

sim-step: ## Advance N ticks deterministically: make sim-step N=4
	@for i in $$(seq 1 $(or $(N),1)); do curl -fsS -X POST $(SIM_URL)/admin/step >/dev/null || exit 1; done
	@curl -fsS $(SIM_URL)/v1/instance && echo

# --- Phase 0 tooling (standalone simulator) ------------------------------------------------

sim-up: ## Start the simulator alone (paused), as in the organizer snippet
	$(SIM_COMPOSE) up -d
	@for i in $$(seq 1 30); do curl -fsS $(SIM_URL)/v1/health && echo && exit 0; sleep 1; done; echo "simulator not healthy" >&2; exit 1

sim-down: ## Stop the standalone simulator
	$(SIM_COMPOSE) down

sim-probe: ## Record contract fixtures from the simulator (RESETS it)
	SIMULATOR_URL=$(SIM_URL) $(SCRIPT_PY) scripts/probe_simulator.py

sim-experiments: ## Run the Phase 0.5 experiments (RESETS it); SLOW=1 adds SSE keepalive/slow-consumer
	SIMULATOR_URL=$(SIM_URL) $(SCRIPT_PY) scripts/sim_experiments.py $(if $(SLOW),--slow)
