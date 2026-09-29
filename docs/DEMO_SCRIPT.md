# Demo script — the brief's 14-step story (§22)

Console http://localhost:3000 · Grafana http://localhost:3001 · commands from the repo root in bash.
Log in on the console as **admin** (password `ADMIN_PASSWORD` in `.env`) so the approve and
failure-injection buttons are enabled. ~8 minutes.

## Before the demo: clean start (2 minutes before)

1. `make up` — builds if needed, waits until every container is healthy (`make ps` to check).
2. `make sim-reset` — tick 0, PAUSED, all events, faults and allocations wiped. Also clears
   anything left from rehearsals.
3. `make sim-status` — must show `"tick":0`, `"status":"PAUSED"`, `events: []`, `active faults: none`.
4. Console: tick 0 · PAUSED, **● LIVE**, FORECAST policy badge, service level 100 %, System
   Status all green, Resilience panel all OK, no open alerts.
5. Grafana: "JALANI Overview" opens as the home page.
6. Pace: `make demo` restarts the simulator at 2 ticks/s (calmer on stage), resets and starts it.
   At the default speed (`make sim-run`, ~7 ticks/s) the world runs dry after ~75 s.
   Tip: `make sim-pause` / `make sim-step N=4` let you freeze or advance time while you talk.

## The 14 steps

| # | Brief step | Do | Say (criterion) |
|---|---|---|---|
| 1 | Normal operations | `make demo` (or `make sim-run`) | "Everything you see is the organizer's simulator, labelled SIMULATED; we never touch real infrastructure." (Problem understanding) |
| 2 | Operator dashboard | Walk the console top to bottom: map, inventories and time-to-stockout, regional demand, incoming supply, System Status | "One screen for the operations center: network, risk, supply, health." (Working product & UX) |
| 3 | Demand starts increasing | Resilience panel → **Dhaka demand spike ×3** (or `make demo-spike`) | "A crisis event: demand in Dhaka triples." |
| 4 | System detects risk | Mirpur/Tongi cells get **▲ spike**, the map nodes turn red and pulse, the shortage alert fires | "Detection on the first tick: observed demand is 2.9× the profile, z ≈ 25." (Intelligence) |
| 5 | Intelligence predicts shortage | Point at a cell: hours to stockout + probability in 24 h; hover for the P10–P90 forecast | "Forecast with uncertainty; our backtest error is ~5 % — the simulator's noise floor — even during the spike." (Intelligence) |
| 6 | Allocation recommendation generated | Recommendations panel: most urgent first, fits each depot's dispatch capacity; the rest wait for next tick | "Every card can actually be approved: dispatch capacity, stock, tank room and routes are already enforced." (Decision quality) |
| 7 | Operator inspects recommendation | Open **signals, constraints, alternatives** on the top card; show before → after, the binding constraint, confidence 30 %, **Human review required** | "Why it's at risk, which signals, which constraint set the quantity, what changes, how sure we are, and the alternatives." (§9) |
| 8 | Allocation is simulated | Tick the review box → **Approve after review** → message *Allocation #n PENDING*; next tick it is IN_TRANSIT, the dot moves along the route on the map; Decision history shows who approved, risk before → after | "Human approval is mandatory; the write is idempotent; outcomes are tracked back into the audit." (Architecture & integration) |
| 9 | Crisis event occurs | `make demo-disrupt` (route Gazipur → Mirpur disrupted from now) | "A regional disruption hits the busiest route." |
| 10 | System adapts | Route turns red/dashed; Mirpur recommendations switch to the cross-region Patiya route; signal explains why | "Even though the route still says AVAILABLE this tick, a shipment departing into the disruption would fail and lose its fuel — we route around it." (Resilience & crisis response) |
| 11 | Application or dependency failure is injected | Resilience panel → **Take prediction service down 60 s** | "Now we break our own ML service." |
| 12 | Monitoring detects failure | System Status: Prediction Service **Down**, Decision Engine **Degraded**; system alert; Grafana: decision policy 0, `PredictionFallbackActive` firing | "Health is visible in the product and in monitoring." (Observability) |
| 13 | Fallback / recovery activates | Banner *fallback allocation policy active*; cards say **FALLBACK rule** and remain approvable. Optionally also **Corrupt simulator data 20 s** (invalid data rejected + critical alert) and **Simulator errors 90 %** (cached data banner) | "ML down → fallback; bad data → reject + alert; dependency down → retry, cached state, degraded mode; low confidence → human review. All four rows of §11." (Resilience) |
| 14 | Operations continue | **Clear all injected failures**; badges return to FORECAST and LIVE, alerts resolve (✓ resolved list), approve one more shipment | "Recovery is automatic; operations never stopped." |

Close with Grafana (request rate, p95, error rate, CPU/memory, intelligence row, operational
log) and one line on load: ~140 decisions/s at the core-api limit, p95 78 ms at 5 users
(`docs/LOADTEST.md`).

## Backup

- If the live stack misbehaves: `make down && make up`, then the clean start above (2 minutes).
- Screenshots of every step's key state are in `docs/evidence/`.
