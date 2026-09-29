# Data (brief §16)

## Operational data: the simulator only

Every number on the console comes from the organizer's BUP Fuel Supply Simulator or is
computed from it. We use no public datasets and generate no synthetic data for the product.

| Simulator endpoint | Used for |
|---|---|
| `/v1/instance` | tick, simulated time, running/paused, stale-data header |
| `/v1/regions` | regional demand factors |
| `/v1/stations`, `/v1/depots` | inventory, capacity, status, dispatch capacity, demand multiplier |
| `/v1/routes` | available / disrupted routes, transit time, max shipment |
| `/v1/demand-history` | last 12 h of demand per station × fuel (forecasting, detection) |
| `/v1/events` | active and scheduled crisis events (known future demand changes, disruptions, outages) |
| `/v1/allocations` | fuel in flight, allocation lifecycle (decision outcomes) |
| `/v1/supply-arrivals` | incoming depot supply |
| `/v1/metrics` | service level, served and unmet liters |
| `POST /v1/allocations` | the only write: approved shipments |

## Derived data (computed by JALANI)

- Demand level per station × fuel = observed demand ÷ the documented structural demand.
- Demand forecast per tick (P50 and standard deviation) for 24 h, and 24 h P10/P50/P90.
- Stockout probability within 24 h, time to stockout (P50 and a P90/P10 demand range),
  expected unmet liters.
- Abnormal-demand flags (spike/drop, ratio to normal, z-score, first tick of the regime).
- Confidence per prediction, with reasons.
- Recommendations and their explanations; decision audit records; system alerts.

## Documented constants we rely on

From the organizer's Integration Guide (§8.5–§8.6), each **verified against the live
simulator in Phase 0** (`docs/SIMULATOR_NOTES.md`, `docs/experiments/`):

- demand profiles in liters per simulated day and noise level per profile;
- hour-of-day factors (verified: **not** normalized, daily demand = profile × mean factor);
- noise is **uniform** ±noise (verified from residuals), which our regime detector uses.

## Recorded data (tests and evidence only)

| File | What |
|---|---|
| `services/common/tests/fixtures/*.json` | 119 recorded simulator responses (every endpoint, error and fault) for contract tests |
| `docs/experiments/raw/e1_demand_rows.json` | 96 ticks of real demand rows (no actions, no events) |
| `docs/experiments/raw/backtest_rows.json` | 120 ticks of real demand with a ×3 Dhaka spike (ticks 40–80), recorded by `scripts/backtest_forecast.py --record` |

Some unit tests multiply real recorded rows by 3 to emulate a spike on a chosen tick; this
is test input only and is labelled in the tests.

## Forecast quality (backtest on recorded simulator data)

`scripts/backtest_forecast.py`, results in `docs/experiments/backtest_results.json`
(mean absolute percentage error, 1 tick / 8 ticks ahead):

| Period | Our model | Profile only | Last value | 8-tick mean |
|---|---|---|---|---|
| before spike | 4.5 % / 4.3 % | 4.4 % / 4.3 % | 8.2 % / 31.9 % | 14.6 % / 40.8 % |
| during ×3 spike | **4.6 % / 4.5 %** | 66.7 % / 66.7 % | 10.4 % / 42.2 % | 24.6 % / 49.3 % |
| after spike | **4.7 % / 4.6 %** | 4.6 % / 4.6 % | 13.1 % / 72.0 % | 39.1 % / 129.1 % |
| other region | 5.7 % / 5.7 % | 5.6 % / 5.6 % | 8.9 % / 22.0 % | 13.0 % / 26.4 % |

The documented noise sets a floor of ~4–6 % for any model; ours stays on it through the
whole crisis, where the profile alone is 14× worse. Using the known event schedule matters:
without it (detection alone) the model is 6.1 % / 16.6 % during and 9.8 % / 45.1 % after the
spike. Spikes were detected on their first tick for all 6 affected series.

## Assumptions

- Simulated time is naive ISO time; we treat it as UTC.
- One tick = `tick_minutes` from `/v1/instance` (15 min by default).
- An allocation created now departs while the simulator processes the current tick and lands
  `transit_ticks` later; arrivals over capacity are lost; FAILED allocations are not refunded
  (all verified in Phase 0).
- Events are effective for `duration_ticks + 1` ticks (verified).
- The supply schedule ends at tick 212; after that the network runs on stock, so shortages are
  unavoidable late in a run (the console says so instead of looking broken).
