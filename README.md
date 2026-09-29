# JALANI — Fuel Supply Intelligence & Resilience Platform

> **জ্বালানি (Jalani)** = "fuel". BUP CSE FEST 2026 Hackathon Finals — *Build. Deploy. Observe. Respond.*

**⚠️ SIMULATED ENVIRONMENT.** JALANI operates only against the organizer-provided
BUP Fuel Supply Simulator. It never touches real fuel infrastructure.

JALANI is an operations-center platform that observes the simulator in real time,
predicts shortages, recommends and executes explainable allocations, survives crises
and software failures, and proves all of it with metrics:
**Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover.**

## Status

Work in progress. The plan and live progress tracker is [`project.md`](project.md).
Simulator behaviour we verified by experiment is in [`docs/SIMULATOR_NOTES.md`](docs/SIMULATOR_NOTES.md).

## Quick start (simulator only, Phase 0)

```bash
docker compose -f docker-compose.sim.yml up -d
curl -s http://localhost:8000/v1/health
python3 -m venv .venv && .venv/bin/pip install httpx
.venv/bin/python scripts/probe_simulator.py      # refresh contract-test fixtures
.venv/bin/python scripts/sim_experiments.py      # re-run the Phase 0 experiments
```

The full-stack `make up` arrives in Phase 1.

## License

[MIT](LICENSE)
