# Simulator contract fixtures

Each fixture is one HTTP exchange with the simulator:

```json
{
  "_meta": {"source": "recorded | doc-derived", "endpoint": "<logical endpoint>", "...": "..."},
  "request": {"method": "GET", "path": "/v1/depots", "params": null, "json": null},
  "response": {"status": 200, "headers": {"content-type": "application/json"}, "json": []}
}
```

| Location | Source | Produced by |
|---|---|---|
| `fixtures/*.json` | **recorded** from the live simulator (119 files, image `1.0.0`, 2026-09-29) | `make sim-probe` (`scripts/probe_simulator.py`) |
| `fixtures/doc_derived/*.json` | **doc-derived** from the Integration Guide, only for cases a live simulator cannot produce | `scripts/make_doc_fixtures.py` |

Load them with `simfixtures` (`services/common/tests/simfixtures.py`): a recorded
fixture always wins over a doc-derived one with the same name.
`test_sim_fixtures.py` pins the recorded behaviour (see `docs/SIMULATOR_NOTES.md`).

The only doc-derived fixtures left:

- `create_allocation__depot_closed`: depots are only ever `OPEN` or `CONSTRAINED`,
  so `DEPOT_CLOSED` cannot be provoked. The error `message` text is a placeholder.
- `stream_event__simulator_notice_error`: needs a background-runner exception.

Re-recording (`make sim-up && make sim-probe`) resets the simulator several times;
`recorded_at`, `elapsed_ms` and wall-clock timestamps change, nothing else should.
