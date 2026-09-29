# JALANI developer entry points. `make help` lists every target.
SHELL := /bin/bash
.DEFAULT_GOAL := help

SIM_URL ?= http://localhost:8000
SIM_COMPOSE := docker compose -f docker-compose.sim.yml
SCRIPT_PY := uv run --no-project --with httpx python

.PHONY: help sim-up sim-down sim-probe sim-experiments

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sim-up: ## Start the simulator alone (paused), as in the organizer snippet
	$(SIM_COMPOSE) up -d
	@for i in $$(seq 1 30); do curl -fsS $(SIM_URL)/v1/health && echo && exit 0; sleep 1; done; echo "simulator not healthy" >&2; exit 1

sim-down: ## Stop the standalone simulator
	$(SIM_COMPOSE) down

sim-probe: ## Record contract fixtures from the simulator (RESETS it)
	SIMULATOR_URL=$(SIM_URL) $(SCRIPT_PY) scripts/probe_simulator.py

sim-experiments: ## Run the Phase 0.5 experiments (RESETS it); SLOW=1 adds SSE keepalive/slow-consumer
	SIMULATOR_URL=$(SIM_URL) $(SCRIPT_PY) scripts/sim_experiments.py $(if $(SLOW),--slow)
