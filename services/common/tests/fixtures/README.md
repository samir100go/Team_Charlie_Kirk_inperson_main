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
| `fixtures/*.json` | **recorded** from a live simulator | `make sim-probe` (`scripts/probe_simulator.py`) |
| `fixtures/doc_derived/*.json` | **doc-derived** from the Integration Guide examples | `scripts/make_doc_fixtures.py` |

**Precedence:** a recorded fixture replaces the doc-derived fixture with the same
file name. The contract-test loader (Phase 2.1) must always prefer `fixtures/<name>.json`.

Doc-derived caveats (see each file's `_meta.inferred`):

- Error `message` texts are not documented; they are placeholders.
- Only one supply arrival, event, allocation and demand row example is documented.
  The real lists are longer.
- The response bodies of `/admin/run|pause|toggle|reset` and `POST /admin/faults`
  are not documented, so those only exist as recorded fixtures.
- `DEPOT_CLOSED` cannot be provoked. Depots are only ever `OPEN` or
  `CONSTRAINED`, so it only exists as a doc-derived fixture.
