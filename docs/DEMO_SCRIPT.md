# Demo script (thin slice)

Operator page: http://localhost:3000 · Grafana "JALANI Overview": http://localhost:3001 (opens without login) ·
core-api: `CORE_API_PORT` from `.env` (8080 by default; 8088 on the dev laptop).
All commands run from the repo root in Git Bash (or any bash with `make` and `curl`).

## Before the demo: clean start (2 minutes before)

1. `make up` — builds if needed and waits until every container is healthy (skip if already up; `make ps` shows health).
2. `make sim-reset` — hard reset: tick 0, **PAUSED**, all events, faults and allocations wiped, supply schedule restored. This also removes any crisis left over from rehearsals (a leftover `demand_spike` is what made demand rates look 3× too high).
3. `make sim-status` — must show `"tick":0`, `"status":"PAUSED"`, `events: []`, `active faults: none`.
4. Open http://localhost:3000 — within 2 s it shows `tick 0 · … · PAUSED`, pill **LIVE**, service level 100.0 %, no "Crisis events" bar, no "demand ×" tags, and Gazipur/Patiya "dispatch free" at 12,000 / 11,000 L.
5. Open http://localhost:3001 — "JALANI Overview" loads as the home page; service level 100 %, simulator reachable = Yes.
6. When you start talking: `make sim-run` (speed 8 ≈ 7 ticks/s; the world runs dry after ~75 s). For a calmer pace use `make demo` instead of steps 1–2 and 6: it restarts the simulator at `DEMO_SPEED` (2 ticks/s), resets it and starts it. `make up` afterwards returns to speed 8.

## The demo (6 steps)

1. **Normal ops:** SIMULATED badge, live tick/time, service level and unmet liters from `/v1/metrics`, and each station × fuel cell with hours to stockout from current inventory ÷ recent demand rate (teal = over 24 h). Same numbers in Grafana.
2. **Crisis:** `make demo-spike` (Dhaka ×3 for 48 ticks, starting at the current tick) → the "Crisis events" bar appears, Mirpur/Tongi get a "demand ×3" tag, and their cells turn red.
3. **Decide:** each recommendation states its math: `cover = (now + incoming + shipment) L / rate`. Recommendations are served most-urgent-first within each depot's dispatch capacity for this tick, so every **Approve** button can succeed; the rest sit under "Waiting for next tick". Click **Approve** → POST `/v1/allocations` with idempotency key `jalani-<recommendation id>` (a double click can't double-ship); the depot's "dispatch free" drops.
4. **Execute:** the allocation appears PENDING, then **IN_TRANSIT** on the next tick with its ETA; the cell shows `+liters` in flight and the station leaves the recommendation list.
5. **Failure:** `make demo-fault` (error_rate 0.9 for 60 s) → red banner "Simulator unavailable – showing cached data" with the data's age; Approve is disabled; Grafana shows simulator call failures and "reachable = No".
6. **Recover:** `make demo-clear` → banner disappears, pill returns to LIVE, tick and allocation status catch up within a second.
