# Requirements traceability

## §19 Required deliverables

| # | Deliverable | Status | Proof |
|---|---|---|---|
| 1 | Working application | DONE | `make up` from a clean clone → all containers healthy; console http://localhost:3000; full loop observe → predict → recommend → approve → simulate → monitor → recover (`docs/DEMO_SCRIPT.md`) |
| 2 | Source repository (code, setup, dependencies, deployment instructions) | DONE | `README.md` (requirements, quick start, URLs, logins, deployment), `.env.example` (every variable documented), `Makefile`, `docker-compose.yml`, `pyproject.toml`/`uv.lock`, `web/package.json`/`pnpm-lock.yaml` |
| 3 | Simulator integration | DONE | core-api reads all `/v1/*` endpoints each tick with timeout/retry/validation (`services/core_api/core_api/slice.py`, `sim_models.py`), writes `POST /v1/allocations` idempotently; 119 recorded contract fixtures + `test_sim_fixtures.py`, `test_sim_validation.py`; simulator behaviour verified by experiment (`docs/SIMULATOR_NOTES.md`) |
| 4 | Intelligence component | DONE | `services/intelligence`: demand forecast with P10/P50/P90, stockout probability and time range, spike/drop detection, confidence; decision engine with capacity-aware priority allocation (`decision.py`); backtest MAPE 4.5–5.7 % vs 67 % profile-only during a spike (`docs/DATA.md`, `docs/experiments/backtest_results.json`); tests `test_forecast.py`, `test_slice_plan.py` |
| 5 | Operator interface | DONE | Next.js console: map, inventory, station status, regional demand, shortage alerts, projected risk, incoming supply, disruptions, recommended allocations, expected impact, system alerts, decision history, service health (`docs/evidence/console-crisis.png`) |
| 6 | Architecture diagram | DONE | `docs/architecture.svg` (simulator → data/backend → intelligence → decision → application → monitoring), `docs/ARCHITECTURE.md` |
| 7 | Deployment | DONE | `docker compose` with health checks and pinned images; `make up`; CI builds every image on every push (`.github/workflows/ci.yml`); `make rollback VERSION=` |
| 8 | Observability evidence | DONE | Grafana dashboard, Loki log search, 9 alert rules (verified firing to Alertmanager): `docs/OBSERVABILITY.md`, `docs/evidence/grafana-intelligence-alerts-logs.png` |
| 9 | Resilience demonstration | DONE | All four §11 rows implemented, visible and one-click triggerable: `docs/RESILIENCE.md`, `docs/evidence/resilience-*.png` |
| 10 | Load-test evidence | DONE | `loadtest/k6/decision.js`, `make loadtest`, results in `loadtest/results/`, analysis in `docs/LOADTEST.md` |
| 11 | Final demo | DONE (script); rehearse live | `docs/DEMO_SCRIPT.md` follows the §22 14-step story with exact actions and a clean-start checklist |

## §20 Recommended deliverables

| Item | Status | Proof |
|---|---|---|
| CI/CD | DONE (CI) | GitHub Actions: lint, typecheck, tests, gitleaks, Docker builds |
| Automated tests | DONE | 96 Python tests (contract, forecast, projection, decision, validation, auth); web lint + typecheck |
| Experiment tracking / model versioning | PARTIAL | `model_version` on every prediction and `/version`; backtest script + recorded results |
| Decision audit history | DONE | Postgres `decisions` + console "Decision history" |
| Deployment versioning | DONE | `GIT_SHA`, `BUILD_TIME`, `IMAGE_TAG` baked into images, `/version` |
| Scenario configuration | DONE | `make demo-spike/-disrupt/-outage/-fault`, console admin triggers |
| Automated fallback | DONE | fallback policy on prediction failure, cached state on simulator failure |
| Rollback | PARTIAL | `make rollback VERSION=<image tag>` |

## §23 Evaluation criteria

| Criterion | Where to look |
|---|---|
| Working Product & UX (20 %) | Console (`README.md` screenshot), demo steps 1–2, 6–8, 14 |
| Intelligence & Decision Quality (20 %) | `docs/DATA.md` backtest, recommendation explanations (§9), capacity-feasible allocation, disruption-aware routing, review on low confidence |
| Architecture & Integration (15 %) | `docs/ARCHITECTURE.md`, `docs/SIMULATOR_NOTES.md`, contract validation, idempotent writes |
| DevOps & Engineering Quality (15 %) | `docker compose` + `make`, CI, pre-commit, tests, typed code (mypy strict) |
| Resilience & Incident Response (10 %) | `docs/RESILIENCE.md`, Resilience panel, demo steps 9–14 |
| Observability & Performance (10 %) | `docs/OBSERVABILITY.md`, `docs/LOADTEST.md`, System Status panel |
| Demo & Problem Understanding (10 %) | `docs/DEMO_SCRIPT.md`, `docs/SIMULATOR_NOTES.md` (21 verified simulator behaviours) |

## §24 Guardrails

| Guardrail | How |
|---|---|
| Only the simulation environment | the only upstream is the simulator container |
| No real infrastructure, purchases, dispatches, credentials | none exist in the code; secrets are generated locally by `make .env` |
| Distinguish simulated results | SIMULATED ENVIRONMENT badge on every screen; README warning |
| Document assumptions | `docs/DATA.md` §Assumptions, `docs/SIMULATOR_NOTES.md` |
| Human review for consequential decisions | nothing ships without an operator approval; low confidence requires an explicit review; enforced in the API |
