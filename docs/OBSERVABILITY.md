# Observability (brief §14)

Open **http://localhost:3001** → "JALANI Overview" (home dashboard, no login needed).

| Layer | What we measure | Where |
|---|---|---|
| Application | core-api request rate, p95 latency and error rate per route (`jalani_http_requests_total`, `jalani_http_request_duration_seconds`); simulator call outcomes (`jalani_sim_requests_total{outcome=ok,retry,failed}`), availability and data age | Grafana rows 1–2; System Status panel in the console (p95, error rate) |
| System | container CPU and memory for every service (cAdvisor) | Grafana "Container CPU / memory" |
| Intelligence | forecast error `jalani_forecast_mape` (1-tick, rolling day), confidence `jalani_prediction_confidence*`, shortage-alert rate `jalani_shortage_alerts_total`, decision frequency `jalani_decisions_total{result}`, fallback activations `jalani_fallback_activations_total`, decision policy, abnormal-demand series, review queue, predict latency/failures | Grafana "Intelligence (brief §14)" row |
| Logs | JSON logs from every service, shipped by Grafana Alloy to Loki; events include `decision.approved/rejected/failed`, `integration.simulator_failed/_recovered`, `integration.invalid_payload`, `integration.database_failed/_recovered`, `fallback.activated/recovered`, `shortage.alert`, `alert.raised/resolved`, `chaos.*`, `auth.login/_failed`, `cache.restored` | Grafana "Operational log" panel; Explore → Loki: `{compose_service="core-api"} \| json \| event=~"decision.*"` |
| Alerts | 9 Prometheus rules → Alertmanager: `JalaniTargetDown`, `JalaniServiceErrors`, `SimulatorUnavailable`, `SimulatorInvalidPayload`, `DatabaseUnavailable`, `PredictionFallbackActive`, `LowPredictionConfidence`, `ForecastErrorHigh`, `StationShortageRisk` | Grafana "Firing alerts" table; http://localhost:9090/alerts; http://localhost:9093 |
| Health | `/healthz`, `/readyz` (dependency checks, degraded vs down), `/metrics`, `/version` on every service; System Status panel in the console | console, `docker compose ps` |

Evidence: `docs/evidence/grafana-intelligence-alerts-logs.png` (captured during a prediction
outage: decision policy 0 = fallback, 2 fallback activations, load-test spike visible);
`PredictionFallbackActive` fired 18 s after the outage started and reached Alertmanager;
Loki held 19 event types from the demo run (decisions, integration failures, recoveries,
alerts, logins).
