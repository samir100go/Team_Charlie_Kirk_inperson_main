# Architecture

![JALANI architecture](architecture.svg)

**simulator → data/backend → intelligence → decision → application → monitoring**

## Components

| Layer | Component | Responsibility |
|---|---|---|
| Simulator | `simulator` (organizer image, unmodified) | The world: stations, depots, routes, demand, supply, events, faults. Only `POST /v1/allocations` writes to it. |
| Data & backend | `core-api` world poller (`services/core_api/core_api/slice.py`) | Polls `/v1/instance` every 0.5 s; on each new tick (or every 2 s) fetches regions, stations, depots, routes, events, metrics, allocations, supply arrivals and 12 h of demand history. Every call: 2 s timeout + one retry. Every payload is validated against a contract (`sim_models.py`); an invalid one rejects the whole refresh. Keeps the last good world in memory and in Redis. |
| | Postgres (`store.py`) | Decision audit (who, when, what, policy, confidence, review, risk before/after, simulator response, outcome) and system alerts. If Postgres is down, writes are buffered and flushed on recovery. |
| | Redis | Last-good world snapshot, so a restarted core-api has cached state before the simulator answers. |
| Intelligence | `intelligence` (`services/intelligence`) | Stateless `POST /v1/predict`: per station × fuel, a 96-tick demand forecast with uncertainty, 24 h P10/P50/P90, stockout probability and time-to-stockout range (`jalani_common/projection.py`), abnormal-demand detection, confidence with reasons, 1-tick forecast error tracking. |
| Decision | decision engine (`services/core_api/core_api/decision.py`) | Ranks at-risk station × fuel series by urgency and allocates each depot's per-tick dispatch capacity, stock and the station's free room (capacity − inventory − fuel in flight) most-urgent-first; avoids routes that are disrupted or will be at departure. Builds the explanation (why, signals, constraints, impact before → after, confidence, alternatives). Falls back to a recent-rate rule when predictions are unavailable. |
| | approval / executor | Operator (or admin) login; low-confidence recommendations require an explicit review; approval sends an idempotent `POST /v1/allocations` (key = recommendation id) with one retry; outcome is reconciled from `/v1/allocations` into the audit. |
| Application | `web` (Next.js) | Operator console, polling core-api through server-side route handlers (the login token lives in an httpOnly cookie; proxies are allowlisted). |
| Monitoring | Prometheus, Alertmanager, Grafana, Loki + Alloy, Tempo + OTel collector, cAdvisor, k6 | Metrics from every service (`/metrics`), container metrics, JSON logs shipped to Loki, provisioned dashboard and alert rules. |

## Data flow per tick

1. core-api sees a new tick → fetches the world → validates → caches (memory + Redis).
2. core-api sends the world to intelligence → predictions for the current tick.
3. The console asks for `/api/v1/state` (1 s) → the decision engine plans on the latest world
   + predictions → recommendations with explanations.
4. The operator approves → core-api re-checks the recommendation is still current, sends the
   allocation, records the decision, and forces a refresh so the allocation shows at once.
5. Every refresh the reconciler copies allocation status (PENDING → IN_TRANSIT → ARRIVED/FAILED)
   onto the decision record.

## Design decisions

- **REST is the source of truth; no SSE dependency.** Phase 0 showed the simulator's SSE never
  announces departures, arrivals or supply (`SIMULATOR_NOTES.md` #1-#2), so polling + per-tick
  refresh is both simpler and correct.
- **Stateless prediction service.** core-api sends the world it sees, so intelligence can be
  restarted, scaled or taken down without losing state, and the fallback is a clean switch.
- **Structural model over a black box.** The simulator's demand is documented structure ×
  noise; we learn the level online and detect regime changes. That gives noise-floor accuracy
  with explanations an operator can check (see `DATA.md`, backtest).
- **Capacity-feasible recommendations.** Every recommendation shown can be approved: dispatch
  capacity, stock, room and route availability (including disruptions starting now) are all
  enforced before a card is shown.
- **Every dependency is soft.** The simulator, intelligence, Postgres and Redis can each fail;
  core-api degrades (cached state, fallback policy, buffered audit) instead of going down.
- **Human in the loop.** Nothing is shipped without an operator action; low confidence adds an
  explicit review step. The platform never auto-executes decisions.
