# Demo script (thin slice) — open http://localhost:3000 after `make up && make sim-reset && make sim-run`

1. **Normal ops:** the SIMULATED badge, live tick/time, service level and unmet liters from `/v1/metrics`, and every station × fuel cell showing hours to stockout (teal = over 24 h).
2. **Crisis:** `curl -X POST localhost:8000/admin/events -H 'content-type: application/json' -d '{"type":"demand_spike","start_tick":0,"duration_ticks":60,"parameters":{"region_ids":["region-dhaka"],"multiplier":3}}'` (set `start_tick` to the current tick) → Mirpur/Tongi cells turn red as demand triples.
3. **Decide:** each red cell gets a one-line recommendation (depot, route, liters, hours of cover after refill); click **Approve** → it POSTs `/v1/allocations` with idempotency key `jalani-<rec id>` (a double click can't double-ship).
4. **Execute:** the allocation appears as PENDING, then **IN_TRANSIT** on the next tick with its ETA; the cell shows `+liters` in flight.
5. **Failure:** `curl -X POST localhost:8000/admin/faults -H 'content-type: application/json' -d '{"type":"error_rate","duration_seconds":60,"parameters":{"rate":0.9}}'` → red banner "Simulator unavailable – showing cached data" with the data's age; the page keeps the last good snapshot and disables Approve.
6. **Recover:** `curl -X POST localhost:8000/admin/faults/clear` → banner disappears, the pill returns to LIVE, and the tick and allocation status catch up within a second.
