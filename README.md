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

Needs Docker with the compose plugin and [uv](https://docs.astral.sh/uv/).

```bash
make sim-up            # organizer simulator snippet, paused, on :8000
make sim-probe         # record contract-test fixtures (resets the simulator)
make sim-experiments   # re-run the Phase 0 experiments (resets the simulator)
```

The full-stack `make up` arrives in Phase 1.

## License

[MIT](LICENSE)
