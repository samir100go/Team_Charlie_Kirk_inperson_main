# Simulator Notes

What we have **confirmed by experiment** about the BUP Fuel Supply Simulator, where it
differs from the Integration Guide (`docs/brief/BUP_Fuel_Supply_Simulator_Integration_Guide_Final.pdf`),
and what that means for our design. Owner: P1.

- **Image:** `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`
  (`sha256:7067050693f49d377d91ca91f2faa6e63f673f69a69429d4693e78e9c0598e92`), scenario `baseline` v1.0, seed 12345.
- **Recorded:** 2026-09-29, simulator standalone (`make sim-up`), paused, driven with `/admin/step`.
- **Evidence:** `make sim-probe` → 119 recorded fixtures in `services/common/tests/fixtures/`;
  `make sim-experiments SLOW=1` → `docs/experiments/phase0_results.json` (experiments E1–E12, raw series in `raw/`).
  `services/common/tests/test_sim_fixtures.py` pins the behaviour below, so a changed simulator breaks the build.
- Every number here is reproducible: same image + seed + actions ⇒ identical results (verified, E1).

## Tooling status (checked 2026-09-29, Docker machine)

| MCP / tool | Status |
|---|---|
| context7, gsap, magicui, playwright | live (`claude mcp list`) |
| GitHub MCP | not connected; using the existing `origin` repo |
| claude.ai Canva | needs auth; not needed |
| Docker 28.5.2, Compose v2.40.3, Buildx v0.29.1 | work without admin |
| uv 0.12.20 (Python 3.12.14 pinned in `.python-version`), pnpm 10.34.6, Node 22.20.0, GNU Make 4.4.1 | OK |

---

## TL;DR — what the simulator does differently from its docs

| # | Topic | Guide says | Simulator actually does | Impact |
|---|---|---|---|---|
| 1 | **SSE `allocation.status_changed`** | on created / departed / arrived / failed / cancelled | **only on API calls** (create, cancel). Departures, arrivals and failures emit nothing | Poll `/v1/allocations` on every tick; SSE ≠ lifecycle feed |
| 2 | **SSE `inventory.updated`** | when depot inventory changes | **only on API calls** (create deducts, cancel refunds). Supply arrivals emit nothing | Refresh depots on every tick |
| 3 | **Overflow on arrival** | not documented | arrival is **clamped to capacity; the excess is destroyed**. Same at depots for supply arrivals | Never ship more than `capacity − inventory − in-flight`; ship out before supply lands on a full depot |
| 4 | **DESTINATION_CAPACITY check** | `inventory + quantity > capacity` | literally that: **ignores fuel already in transit** | The simulator will happily accept shipments that overflow; our optimizer must count in-flight |
| 5 | **FAILED allocations** | "FAILED if route disrupted at departure" | FAILED with `failure_reason: "ROUTE_UNAVAILABLE"`, **depot is NOT refunded**, no audit entry | A failed shipment loses the fuel. Cancel PENDING allocations on a route that is about to be disrupted |
| 6 | **Idempotent replay** | 201 (§5.4) vs 200 (§9 cheat sheet) | **201**, same id, current state (even CANCELLED) | Treat 200 and 201 alike |
| 7 | **Hour-of-day factors** | not stated | **not normalized**: daily demand = profile × mean(hour factor) → urban_high ×0.95, highway ×0.975 | Use raw factors; system demand ≈ D 40.9k / P 34.2k / O 17.9k L/day, not 41.6k/35.1k/18.4k |
| 8 | **Event duration** | `end_tick = start_tick + duration_ticks` | event is effective for **`duration_ticks + 1`** ticks (processing ticks `start…end` inclusive) | Off-by-one in any "time to recovery" we show |
| 9 | **`shipment_delay` / `supply_shortfall`** | "one-shot" | applied to **every future arrival** matching the filters (a shortfall on one depot halved all 11 remaining arrivals) | One event can remove ~25% of all remaining supply — forecast it as such |
| 10 | **`depot_constraint`** | "signals reduced capacity" | **only a signal**: dispatch capacity, inventory and admission are unchanged | Show it, don't plan around a reduced cap |
| 11 | **Dispatch capacity** | "in-flight + pending on this tick" | only allocations **created in the current tick** count; cancelling one frees its share | Full `dispatch_capacity_per_tick` is available every tick |
| 12 | **Station outage** | served drops to 0 | served 0 **and the demand counts as unmet** (hurts service level); deliveries to an OUTAGE station still arrive | Outages cost service level no matter what we do; pre-fill doesn't help during the outage |
| 13 | **`sim_time`** | ISO with `+00:00` | **naive** ISO (`2026-01-01T00:15:00`) on instance, step, demand rows, SSE. Fault/audit `wall_time`s differ (aware / naive) | Parse as UTC explicitly |
| 14 | **`stale_data`** | header on `/v1/*` GETs; "the SSE stream itself does not" | header **also on the SSE response**; data is **not** frozen (tick keeps moving) | The header is the only signal; data is still fresh |
| 15 | **`stream_disconnect`** | `GET /v1/stream` → 503 | only **new** connections get 503; **already-open streams keep flowing** | Chaos demo must force a reconnect to show it |
| 16 | **`unavailable` / `error_rate`** | 503 on `/v1/*` | also on **POST /v1/allocations** and the stream. A 503 on POST has **no side effect** (0/9 created) | Retry POST with the same key is safe |
| 17 | **Supply schedule** | 22 arrivals | 22 arrivals, **last at tick 212** (~53 sim-hours). Then nothing, ever | The world runs dry: no-action service level is 0.30 by tick 300. See "World budget" |
| 18 | **Speed** | `SIMULATION_SPEED=8` ticks/s | **~7.2 ticks/s** (0.14 s per tick) → 1 sim-day ≈ 13.3 s | Budget ≤ 100 ms per decision cycle still holds |
| 19 | **404 bodies** | `{"detail":{"code":"NOT_FOUND"}}` | GET/cancel 404s: `{"detail":{"code":…}}` with **no message**; POST 404 **has** a message ("Source depot not found") | `message` is optional in the parser |
| 20 | **Undocumented endpoints** | — | `GET /` (name/version) and `GET /admin/stats` (row counts, SQLite path, scenario file) | `/admin/stats` is handy for the system page |
| 21 | **Reset while RUNNING** | "hard reset" | also sets status **PAUSED** | After reset, call `/admin/run` if we want it running |

---

## 1. Tick semantics and order of operations

"State T" = what the REST API returns after `/admin/step` returned `{"tick": T}` (and `instance.tick == T`).
One step **processes tick T−1** and then moves the clock to T. Inferred order inside one processed tick `t`
(consistent with every experiment):

1. Events with `start_tick == t` become ACTIVE (route → DISRUPTED, station → OUTAGE, multiplier ×, supply rescheduled/cut).
2. PENDING allocations depart: `departure_tick = t`, `expected_arrival_tick = t + transit_ticks`. If the route is DISRUPTED → FAILED (`ROUTE_UNAVAILABLE`), no refund.
3. Arrivals with `expected_arrival_tick == t` (allocations) and `planned_tick == t` (supply) are added, **clamped to capacity**.
4. Demand for tick `t` is drawn and served from station inventory (0 served if OUTAGE). One demand row per station × fuel, `tick = t`, `sim_time = t × 15 min`.
5. Events with `end_tick == t` RESOLVE (reversed). Because this happens after demand, the event affected ticks `start…end` inclusive.
6. `tick = t + 1`; SSE `simulation.tick {"tick": t+1, "sim_time": …}`.

Consequences:
- An allocation created at state T departs in the very next step and is **visible as ARRIVED after `transit_ticks + 1` steps** (Mirpur: 3 steps, Cox's Bazar: 4, cross-region: 5).
- The guide's example (`created 5, departure 6`) is wrong for a paused world: `departure_tick == created_tick`.
- An event injected with `start_tick = current tick` shows `SCHEDULED` until the next step. Admission checks during that window still pass (a POST to a station whose outage starts "now" returns 201).
- Inventory drop between state T−1 and T equals `served_liters` of demand row `tick = T−1` (exact, 0.0 error on all 12 series).

## 2. Answers to the §4.6 questions

| Question | Answer | Evidence |
|---|---|---|
| Idempotent replay: 200 or 201? Same key + different body? | **201**, same `id`, returns the current state (PENDING / IN_TRANSIT / CANCELLED). `quantity` 3000 vs 3000.0 is the same body. Different body → **409 `IDEMPOTENCY_KEY_MISMATCH`**, also for a cancelled key. Key > 150 chars → 422 | E2, `create_allocation__replay*` |
| Are hour factors normalized? | **No.** Daily totals match `profile × region_factor × multiplier × mean(hour factor)` within noise (urban_high 0.944–0.950, highway 0.966–0.980, industrial/regional ≈1.0 because their means are exactly 1.0). Hour boundaries match §8.6 exactly | E1 |
| Overflow on arrival? | **Clamped to capacity, excess lost.** Two 2500 L shipments into Tongi OCTANE (3500/6000): first received 2500, second received 20.698 (audit `metadata_json.received`). Depots behave the same for supply | E3, 300-tick run |
| Does CONSTRAINED reduce dispatch? | **No.** 12,000 L accepted per tick both OPEN and CONSTRAINED; status flips after 1 step | E4 |
| Is demand independent of our actions? | **Yes.** Same seed, with vs without allocations: 0 of 1152 demand rows differ (served differs, of course). Deterministic across resets | E1 |
| `route_disruption` + PENDING allocation | FAILED at departure, `failure_reason: "ROUTE_UNAVAILABLE"`, **no refund**. IN_TRANSIT shipments on a route that becomes DISRUPTED **still arrive** | E5, E12c |
| Cancel refunds inventory? | **Yes**, immediately, and frees this tick's dispatch share | E5, E12d |
| Every fault type | See §5 | E6, `fault_*` fixtures |
| SSE cadence at speed 8; station `inventory.updated`? | 72 ticks in 10 s (0.140 s p50/p95). Only `simulation.tick` while running; **never** a station `inventory.updated` | E7 |
| `/admin/reset` → tick 0? | **Yes.** Allocations, events, faults, demand rows and metrics wiped; supply back to 22 SCHEDULED; status **PAUSED** (even if it was RUNNING). Publishes `simulator.notice {"message":"Simulation reset"}` | E8 |

## 3. Allocations

- **Validation codes seen live:** `NOT_FOUND` (404), `ROUTE_MISMATCH`, `STATION_CLOSED`, `ROUTE_DISRUPTED`, `ROUTE_CAPACITY_EXCEEDED`, `INSUFFICIENT_INVENTORY`, `DISPATCH_CAPACITY_EXCEEDED`, `DESTINATION_CAPACITY_EXCEEDED`, `IDEMPOTENCY_KEY_MISMATCH` (409), `CANNOT_CANCEL` (409), `ALLOCATION_NOT_FOUND` (404), 422 Pydantic lists.
- **`DEPOT_CLOSED` is unreachable:** depots are only ever OPEN or CONSTRAINED. It stays a doc-derived fixture.
- Depot inventory is **deducted at creation** (PENDING), not at departure.
- `INSUFFICIENT_INVENTORY` therefore already accounts for our own PENDING shipments.
- `metrics.allocation_liters` counts IN_TRANSIT + ARRIVED (0 after failures/cancels). `allocation_failures` counts FAILED.
- Numbers: inventories start as ints and become floats once touched; `quantity` and `dispatch_capacity_per_tick` are floats. Parse everything numeric as `float`.

## 4. Crisis events

| Type | Live behaviour |
|---|---|
| `demand_spike` | `station.demand_multiplier` ×= multiplier while ACTIVE, ÷ on resolve. Overlapping spikes **multiply** (2.0 × 1.5 = 3.0) and unwind exactly back to 1.0. Demand rows scale exactly by the multiplier |
| `route_disruption` | route → DISRUPTED; new POSTs → `ROUTE_DISRUPTED`; PENDING → FAILED at departure (no refund); IN_TRANSIT unaffected |
| `station_outage` | station → OUTAGE; POST → `STATION_CLOSED`; demand fully **unmet**; inventory frozen; in-transit deliveries still land |
| `depot_constraint` | depot → CONSTRAINED; no other effect |
| `shipment_delay` | `planned_tick += delay_ticks`, status DELAYED, on **all future** matching arrivals, applied when the event starts. Delayed arrival lands at the new tick (`actual_tick` = new `planned_tick`, status ARRIVED) |
| `supply_shortfall` | `quantity *= factor` on **all future** matching arrivals (empty `fuel_types` = all fuels) |

- Events are visible in `/v1/events` (and `/admin/events`) as SCHEDULED as soon as they are created → plan proactively.
- Bad `type`, `duration_ticks = 0` → 422.

## 5. Faults

All apply to `/v1/*` except `/v1/health`; none apply to `/admin/*`. Faults stack (latency + stale together work). They auto-expire on time (an `unavailable` of 2 s recovered after 2.03 s).

| Fault | GET `/v1/*` | POST `/v1/allocations` | `GET /v1/stream` |
|---|---|---|---|
| `latency` (default 500 ms) | +delay (404 ms at 400) | +delay | opens normally |
| `unavailable` | 503 `{"error":{"code":"FAULT_INJECTED","message":"Simulator API temporarily unavailable."}}` | 503, same body | 503, **error** envelope |
| `error_rate` (default 0.25) | 503 with probability *rate* (22/100 at 0.3, 25/100 default), `"Injected transient API error."` | 503 with probability *rate* (20/40 at 0.5); **no side effect** | opened in our samples |
| `stale_data` | 200 + `x-simulator-stale: true`; data **not** frozen | no header | 200 + **header present** |
| `stream_disconnect` | unaffected | unaffected | 503 `{"detail":{"code":"FAULT_INJECTED"}}` for **new** connections; open streams continue |

- Bad `type`, `duration_seconds = 0` → 422.

## 6. SSE `/v1/stream`

- Headers: `content-type: text/event-stream; charset=utf-8`, `cache-control: no-cache`. First line `: connected`; `: keepalive` after 15 s of silence (measured 15.03 s).
- Events seen: `simulation.tick {tick, sim_time}` (tick = new clock value), `allocation.status_changed` (full allocation object; create + cancel only), `inventory.updated` (`entity_type: "depot"`; create + cancel only), `simulator.notice {"message":"Simulation reset"}`.
- **Slow consumer:** we stopped reading for 35 s (250 ticks) and then received all 250 — TCP buffers absorbed them, so the documented 200-event drop did not trigger at this payload size. It is still documented, so the watchdog stays.
- No `Last-Event-ID` replay (per guide; not contradicted).

## 7. Reads

- `/v1/demand-history`: **newest first** (id-desc). `station_id` is **optional** (omitted → all stations). `limit=0` → 1 row (clamped), `limit=5000` → accepted, no 422 (upper clamp to 2000 not exercised: only 60 rows existed). Unknown `station_id` → **200 `[]`**, not 404.
- `/v1/supply-arrivals`: sorted by `planned_tick`; all 22 rows always present (ARRIVED ones too).
- `/v1/health` stays 200 under every fault.

## 8. Demand model (for forecasting, P1)

`demand(station, fuel, t) = profile_daily[fuel] / 96 × region.demand_factor × station.demand_multiplier × hour_factor(profile, hour(t)) × (1 + U(−noise, +noise))`

- Noise is **uniform**, not Gaussian: residuals stay within ±noise (urban_high −0.0998…+0.0990) and their std matches noise/√3 (0.0573 vs 0.0577).
- Hour factors exactly as §8.6, **not** normalized. `hour(t)` from the row's `sim_time`.
- Demand is a pure function of seed + tick + events, so the P50 forecast is essentially known. The value is in inventory/risk, not in fancy demand models.

## 9. World budget (why shortages are unavoidable)

- Supply: 4 initial arrivals (57,000 L, ticks 12–20) + 3 waves × 6 arrivals (53,000 L each, ticks 64–84, 128–148, 192–212). **Nothing after tick 212.**
- Total fuel ever available ≈ initial depots 252k + stations 86k + supply 216k ≈ 554k L vs ~93k L/day of demand → **≈ 5.5–6 sim-days** even with perfect distribution (Petrol runs out first, ~5.5 days).
- With no allocations: first unmet demand at tick 65 (Tongi DIESEL). At tick 300 every station is empty, **depots sit full at capacity** (surplus supply destroyed), service level 0.299.
- At ~7.2 ticks/s the whole supply schedule plays out in ~30 s wall-clock and the world is dry in ~75 s. Demo runs need a slower speed (§11.4) or `/admin/reset`.

## 10. Lifecycle, restart and reset

- `docker compose restart` keeps the world (SQLite at `/app/data/fuel_simulator.db`, no volume) **and** its RUNNING/PAUSED status — a RUNNING world keeps ticking after restart.
- `docker compose down && up` (container recreated) → fresh world at tick 0, status from `SIMULATOR_START_MODE`.
- Image: Debian 13, user `sim` (uid 1000), **has Python 3.12 (`/opt/venv/bin/python`) and `curl`**, and ships its own `HEALTHCHECK` (`curl -fsS http://127.0.0.1:8000/v1/health`, 30 s interval). The compose healthcheck can rely on either.

## 11. Design implications

**P1 (sim client, ingestor, intelligence, executor)**
- Drive state from `simulation.tick` + full REST refresh (allocations, depots, stations, routes, supply, events) — SSE carries no lifecycle information.
- The ingestor detects departures/arrivals/failures by diffing `/v1/allocations` and emits our own `allocation.*` events.
- The optimizer must use `effective_room = capacity − inventory − in_flight_to_station` (the simulator won't protect us) and keep depots below capacity before a supply arrival (ship out or lose it).
- Before a known `route_disruption` starts, cancel PENDING allocations on that route (refund) instead of letting them FAIL (loss).
- Treat 200 and 201 as success; safe to retry POST on 503/timeout with the same key.
- Error parser: `detail{code, message?}`, `error{code, message}`, `detail[...]`, SSE `detail{code}`.
- Parse `sim_time` as naive → UTC.
- Account for event windows being `duration + 1` ticks.
- Dispatch cap is per tick, not per in-flight volume → ship every tick when needed.

**P2 (API, console, observability, chaos)**
- Expect **permanent shortage after ~5.5 sim-days** and falling service level late in a run. The UI should explain "supply exhausted" instead of looking broken.
- The stale header also appears on the stream response; show a STALE badge from our `meta.is_stale`, not from SSE.
- Chaos demo for `stream_disconnect`: kill our SSE connection (or restart the ingestor) after injecting, otherwise nothing visible happens.
- `/admin/stats` gives row counts for the system page.
- Plan demo speed around ~7.2 ticks/s at `SIMULATION_SPEED=8`.

## 12. Not verified / could not reproduce

- The >200-event SSE queue drop (not triggered at 250 events; see §6).
- `simulator.notice {"level":"error"}` from a background-runner exception (needs an internal failure; doc-derived fixture only).
- `DEPOT_CLOSED` (unreachable; doc-derived fixture only).
- Whether `latency` also delays the SSE handshake (not measured).
