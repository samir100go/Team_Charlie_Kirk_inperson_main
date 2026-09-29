# Resilience (brief §11)

Every row of the brief's table is implemented, visible in the console's **Resilience** panel
(live ACTIVE/OK), raised as a **system alert** (console + Postgres), exported as a
**Prometheus metric with an alert rule**, **logged** to Loki, and **triggerable with one click**
by an `admin` (or from the command line).

| Failure (brief) | Trigger for the demo | Detection | Response | Recovery | Evidence |
|---|---|---|---|---|---|
| **ML model unavailable → fallback allocation policy** | Console: *Take prediction service down 60 s* (intelligence answers 503), or `docker compose stop intelligence` | predict call fails (2 s timeout); `jalani_decision_policy_forecast` = 0 | Decision engine switches to the recent-demand-rate rule; every card says **FALLBACK rule**; banner; Prediction Service = Down, Decision Engine = Degraded; alert `prediction_unavailable` | next successful prediction for the current tick switches back (after a 3 s cool-down); alert resolved | `evidence/resilience-ml-down-fallback.png`; alert `PredictionFallbackActive` fired and reached Alertmanager; log `fallback.activated` / `fallback.recovered`; test `test_every_dependency_is_soft…` |
| **Invalid simulator response → reject input + raise alert** | Console: *Corrupt simulator data 20 s* (core-api corrupts `/v1/stations` at its integration boundary: a string inventory and an unknown status) | Pydantic contract for every payload (`sim_models.py`) | Whole refresh rejected, last valid snapshot kept, **critical** alert naming the bad field; banner; Fuel Simulator = Degraded | first valid refresh resolves the alert | `evidence/resilience-invalid-payload.png`; metric `jalani_sim_invalid_payloads_total`, alert `SimulatorInvalidPayload`; log `integration.invalid_payload`; tests `test_sim_validation.py` (all 10 recorded payload types pass; corrupted rejected) |
| **Prediction confidence too low → human review requested** | Console: *Dhaka demand spike ×3* (or `make demo-spike`), then step/run | confidence < 60 % (regime changed a few ticks ago, or short history) | card shows **Human review required** with reasons; Approve disabled until the reviewer ticks the box; the API refuses unreviewed approvals (409 `REVIEW_REQUIRED`); info alert | confidence recovers after ~8 ticks in the new regime | `evidence/console-crisis.png` (confidence 30–33 %, review boxes); alert `LowPredictionConfidence`; audit field `reviewed`; tests `test_forecast_policy_flags_low_confidence…`, API check |
| **Backend dependency unavailable → retry / cached state / degraded mode** | Simulator: console *Simulator errors 90 % for 60 s* or `make demo-fault`; Database: `docker compose stop postgres` | every simulator call has a 2 s timeout and one retry; DB ping every 2 s | Simulator: last good world served with its age (**CACHED n s old** banner), approvals disabled; Postgres: decisions still go to the simulator, audit records buffered in memory, banner, Database = Down; Redis: last good world restored on core-api restart | automatic on the next good poll / DB ping; buffered audit flushed to Postgres | `evidence/resilience-simulator-down-cached.png`; alerts `SimulatorUnavailable`, `DatabaseUnavailable`; verified: approval while Postgres down → allocation #1 created, decision buffered, written to Postgres after restart |

Clear everything: console *Clear all injected failures* (clears simulator faults, corruption
and the prediction outage) or `make demo-clear`.

## Other mechanisms

- **Idempotent writes:** every approval uses `idempotency_key = jalani-<recommendation id>`,
  so retries and double clicks cannot double-ship (Phase 0: replays return the same allocation).
- **Safe retries:** a 503 from the simulator on `POST /v1/allocations` has no side effect
  (Phase 0), so the executor retries once.
- **Capacity-feasible decisions:** dispatch capacity, stock, station room (including fuel in
  flight: overflow is destroyed) and routes disrupted at departure are enforced before a card is
  shown, so an approval does not fail at the simulator and fuel is not lost.
- **Soft dependencies:** `/readyz` of core-api treats the simulator, intelligence, Postgres and
  Redis as non-critical: the API stays up and reports `degraded`.
- **Health checks everywhere:** every container has a health check; compose starts services in
  dependency order and `make up` waits until all are healthy.
- **Stale alerts on restart** are closed and conditions re-evaluated live.

## Crisis handling (brief §10)

| Crisis | What the system shows |
|---|---|
| Demand spike | spike tags on stations, "demand ×3" signal, forecast adapts on the first tick, risk turns red, recommendations with review until confidence recovers |
| Shipment delay / supply shortfall | incoming-supply panel shows DELAYED / reduced quantities; depot stock in the plan |
| Depot constraint | depot box on the map turns amber; the event appears in signals |
| Regional disruption (`make demo-disrupt`) | route drawn red and dashed; allocations are rerouted to the cross-region backup route, including when the disruption is only scheduled to start this tick (a shipment departing into it would fail and lose its fuel) |
| Station outage (`make demo-outage`) | station grey on the map, no recommendations to it, outage ticks excluded from consumption in the projection |
| Combined | each effect composes; recommendations stay capacity-feasible |
