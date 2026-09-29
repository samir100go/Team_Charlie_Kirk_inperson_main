# Load test (brief §17)

## What is tested

**Path: the end-to-end decision request**, `POST /api/v1/recommendations/compute` on core-api.
One request does the full intelligent loop on the live world:

1. core-api serialises the current world (4 stations, 2 depots, 6 routes, 12 h of demand
   history = ~576 rows, allocations, events) and sends it to the intelligence service;
2. intelligence validates it and computes 12 station × fuel forecasts (96-tick curves with
   uncertainty), stockout projections and anomaly detection (`/v1/predict`);
3. core-api runs the capacity-aware allocation planner and returns every recommendation
   with its explanation (signals, constraints, impact before → after, alternatives).

A second scenario polls the dashboard read model `GET /api/v1/state` with 5 virtual users,
the way operator consoles do, so we also see how reads degrade while decisions are busy.

- Tool: k6 (`grafana/k6:1.8.1`, compose profile `loadtest`), script `loadtest/k6/decision.js`.
- Workload: `constant-vus`, 40 s per level, decision concurrency 5, 20 and 50 VUs, no think time
  (closed loop: each VU fires its next request as soon as the last one returns).
- Environment: the full `docker compose` stack on one Windows 11 laptop (Docker Desktop, WSL2),
  simulator paused at tick 33 during a ×3 Dhaka demand spike (5 recommendations, 7 waiting).
  core-api and intelligence are single uvicorn processes with the compose CPU limits
  (core-api 1.0 CPU, intelligence 2.0 CPU).
- Resource usage: `docker stats` sampled continuously during each run.
- k6 also remote-writes its metrics to Prometheus (`-o experimental-prometheus-rw`), and the
  Grafana "JALANI Overview" dashboard shows core-api request rate, p95 and error rate live.
- Reproduce: `make up && make loadtest` (writes `loadtest/results/`).

## Results (2026-09-29)

| VUs | avg ms | p50 ms | p95 ms | p99 ms | decisions/s | errors | dashboard p95 ms | core-api CPU avg/max | intelligence CPU avg/max | peak MiB core/intel |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 52.4 | 50.2 | 78.3 | 103.3 | 95.4 | 0.00% | 33.3 | 97.3% / 101.1% | 58.9% / 78.7% | 60.4 / 47.5 |
| 20 | 153.3 | 137.6 | 275.2 | 387.0 | 130.2 | 0.00% | 63.3 | 97.0% / 100.7% | 76.7% / 96.5% | 67.8 / 54.5 |
| 50 | 354.0 | 286.5 | 803.7 | 1162.1 | 140.8 | 0.00% | 85.1 | 95.2% / 101.0% | 79.3% / 94.8% | 73.4 / 75.8 |

Raw data: `loadtest/results/decision-vus*.json` (k6 summaries), `stats-vus*.csv` (docker stats),
`report.json`. All k6 thresholds passed (decision error rate < 1 %, decision p95 < 2 s,
dashboard p95 < 1 s). Total requests at 50 VUs: 9,167 (5,636 decisions), 0 failed.

## What the numbers say

- **Capacity: ~140 end-to-end decisions per second**, reached at ~20 concurrent clients.
  Throughput rises from 95/s (5 VUs) to 130/s (20 VUs) and flattens at 141/s (50 VUs).
- **The bottleneck is core-api CPU.** It sits at its 1-CPU compose limit (~100 %) from the
  first level on, while intelligence has headroom (59–79 % of its 2 CPUs). Each decision
  serialises the whole world twice (to intelligence and back to the client, ~33 KB per
  response: 194 MB received for 5,636 decisions) and runs the planner in pure Python.
- **Above the knee, latency is queueing.** Latency grows almost linearly with concurrency
  (avg 52 → 153 → 354 ms, Little's law: 50 VUs / 141 req/s ≈ 355 ms), and p99 passes 1 s at
  50 VUs. Nothing fails; requests wait.
- **Reads stay fast.** The dashboard read model keeps p95 ≤ 85 ms even with 50 concurrent
  decision clients, because it reuses the per-tick prediction instead of recomputing it.
- **Graceful degradation under load.** At 50 VUs one of 5,636 responses (0.02 %) came back
  on the fallback policy: one prediction call missed its 2 s budget, the decision engine
  used the fallback rule for that request and recovered on the next one. No request failed.
- Memory is flat (≤ 76 MiB per service): no leaks or growth with load.

## Where the limits are and how to move them

| Limit | Evidence | Next step if we needed more |
|---|---|---|
| core-api CPU (1 core) | ~100 % CPU at every level, throughput flat at ~140/s | more uvicorn workers or replicas behind a load balancer (state is in Postgres/Redis already), or raise the CPU limit |
| per-request world serialisation | ~33 KB response, world sent to intelligence each time | cache the plan per simulator tick (it only changes once per tick); send deltas to intelligence |
| intelligence CPU | 79 % of 2 CPUs at 50 VUs | vectorise the 96-tick projections (NumPy), add workers |

The operational need is far below the limit: the console polls one plan per second per
operator, and the simulator produces at most ~7 ticks/s, so one core-api instance serves the
demo with >20× headroom.
