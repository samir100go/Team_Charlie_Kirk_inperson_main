"""World cache + decision API: poll the simulator, ask the prediction service, serve the plan.

- Polls /v1/instance every POLL_S; on a new tick (or every FULL_REFRESH_S) refreshes
  regions, stations, depots, routes, events, metrics, allocations and 12 h of demand-history.
  Every call: timeout + one retry. If any call fails the whole refresh is dropped and the
  last good state is kept (brief §11: backend dependency unavailable -> retry, cached state,
  degraded mode).
- After each refresh the world goes to the intelligence service (/v1/predict). If it fails
  or times out, decisions use the fallback rule (brief §11: ML unavailable -> fallback
  allocation policy) until a prediction for the current tick succeeds again.
- Low-confidence recommendations need an explicit review on approval (brief §11).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Path, Query
from prometheus_client import Counter, Gauge
from pydantic import BaseModel

from core_api import chaos
from core_api.auth.tokens import Auth, User
from core_api.decision import FUELS, incoming_schedule, plan
from core_api.sim_models import InvalidSimulatorPayload, validate
from core_api.store import Store

POLL_S = 0.5
FULL_REFRESH_S = 2.0  # also catches /admin/reset (tick back to 0) and new allocations
TIMEOUT = httpx.Timeout(2.0, connect=1.0)
PREDICT_TIMEOUT = httpx.Timeout(2.0, connect=0.5)
PREDICT_COOLDOWN_S = 3.0  # after a failed predict call, don't hammer a sick service
HISTORY_TICKS = 48

log = structlog.get_logger()

SIM_CALLS = Counter(
    "jalani_sim_requests_total",
    "Simulator HTTP calls made by core-api, by endpoint and outcome (ok, retry, failed).",
    ["endpoint", "outcome"],
)
SIM_AVAILABLE = Gauge("jalani_sim_available", "1 if the last simulator poll succeeded.")
SIM_DATA_AGE = Gauge("jalani_sim_data_age_seconds", "Age of the last good simulator snapshot.")
SIM_TICK = Gauge("jalani_sim_tick", "Simulator tick of the last good snapshot.")
SERVICE_LEVEL = Gauge("jalani_service_level", "served / (served + unmet), from /v1/metrics.")
UNMET_LITERS = Gauge("jalani_unmet_demand_liters", "Cumulative unmet demand (L), /v1/metrics.")
SERVED_LITERS = Gauge("jalani_served_demand_liters", "Cumulative served demand (L), /v1/metrics.")
RECOMMENDATIONS = Gauge(
    "jalani_recommendations", "Current refill recommendations by state.", ["state"]
)
APPROVALS = Counter(
    "jalani_allocations_submitted_total", "Approved recommendations sent to the simulator.",
    ["result"],
)  # fmt: skip
PREDICT_CALLS = Counter(
    "jalani_predict_requests_total", "Calls to the prediction service by outcome.", ["outcome"]
)
POLICY_FORECAST = Gauge(
    "jalani_decision_policy_forecast", "1 = forecast policy, 0 = fallback rule in use."
)
FALLBACK_ACTIVATIONS = Counter(
    "jalani_fallback_activations_total", "Switches from the forecast policy to the fallback rule."
)
SHORTAGE_ALERTS = Counter(
    "jalani_shortage_alerts_total", "Station x fuel series entering CRITICAL risk.", ["fuel"]
)
REVIEW_REQUIRED = Gauge(
    "jalani_recommendations_review_required", "Recommendations waiting for human review."
)


INVALID_PAYLOADS = Counter(
    "jalani_sim_invalid_payloads_total", "Simulator responses rejected by validation.", ["endpoint"]
)
CACHE_KEY = "jalani:world:latest"


class SimUnavailable(Exception):
    pass


class ApproveBody(BaseModel):
    reviewed: bool = False  # operator confirms they reviewed a low-confidence recommendation


def _endpoint(path: str) -> str:
    return "/v1/allocations/{id}/cancel" if path.endswith("/cancel") else path


class Slice:
    def __init__(
        self,
        simulator_url: str,
        intelligence_url: str | None = None,
        store: Store | None = None,
        redis_url: str | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(base_url=simulator_url.rstrip("/"), timeout=TIMEOUT)
        self.store = store
        self.redis_url = redis_url
        self.corruption = chaos.Corruption()
        self.invalid: dict[str, Any] | None = None
        self.restored_from_cache = False
        self.intel = (
            httpx.AsyncClient(base_url=intelligence_url.rstrip("/"), timeout=PREDICT_TIMEOUT)
            if intelligence_url
            else None
        )
        self.world: dict[str, Any] | None = None
        self.fetched_at: float | None = None
        self.last_error: str | None = None
        self.sim_stale = False
        self.predictions: dict[str, Any] | None = None
        self.predict_error: str | None = None
        self.predict_ok_at: float | None = None
        self.predict_latency_ms: float | None = None
        self._predict_retry_at = 0.0
        self._policy = "forecast"
        self._pending_alert: tuple[str, str] | None = None
        self._critical: set[tuple[str, str]] = set()
        self._tick: int | None = None
        self._full_at = 0.0

    # -- simulator calls: timeout + exactly one retry -------------------------------------
    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        endpoint = _endpoint(path)
        last = ""
        for attempt in range(2):
            try:
                resp = await self.client.request(method, path, **kw)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__} on {path}"
            else:
                if resp.status_code < 500:
                    SIM_CALLS.labels(endpoint, "ok").inc()
                    return resp
                last = f"HTTP {resp.status_code} on {path}"
            SIM_CALLS.labels(endpoint, "retry" if attempt == 0 else "failed").inc()
        raise SimUnavailable(last)

    async def _get(self, path: str, **params: Any) -> Any:
        resp = await self._call("GET", path, params=params or None)
        resp.raise_for_status()
        if path == "/v1/instance":
            self.sim_stale = resp.headers.get("x-simulator-stale") == "true"
        return validate(path, self.corruption.apply(path, resp.json()))

    # -- polling --------------------------------------------------------------------------
    async def poll_forever(self) -> None:
        await self._restore_cache()
        while True:
            was_down = self.last_error is not None
            try:
                await self.poll_once()
                SIM_AVAILABLE.set(1)
                if was_down:
                    log.info("integration.simulator_recovered", tick=self._tick)
                    await self._alert_resolve("simulator", "simulator_unavailable")
            except InvalidSimulatorPayload as exc:
                INVALID_PAYLOADS.labels(exc.endpoint).inc()
                self.last_error = f"invalid simulator data rejected ({exc.endpoint})"
                self.invalid = {"endpoint": exc.endpoint, "errors": exc.errors, "at": time.time()}
                SIM_AVAILABLE.set(0)
                log.error("integration.invalid_payload", endpoint=exc.endpoint, errors=exc.errors)
                await self._alert(
                    "simulator",
                    "invalid_payload",
                    "critical",
                    f"Rejected invalid simulator data from {exc.endpoint}; "
                    "decisions use the last valid snapshot",
                    {"errors": exc.errors[:5]},
                )
            except (SimUnavailable, httpx.HTTPStatusError) as exc:
                if not was_down:
                    log.warning("integration.simulator_failed", error=str(exc))
                self.last_error = str(exc)
                SIM_AVAILABLE.set(0)
                await self._alert(
                    "simulator",
                    "simulator_unavailable",
                    "critical",
                    f"Simulator unreachable after retry ({exc}); serving cached state",
                )
            if self.fetched_at:
                SIM_DATA_AGE.set(time.time() - self.fetched_at)
            await asyncio.sleep(POLL_S)

    async def maintain_store(self) -> None:
        """Database health, buffered-write flush and decision outcomes, off the poll path."""
        if self.store is None:
            return
        while True:
            if await self.store.ping():
                await self._alert_resolve("database", "database_unavailable")
                await self.store.flush()
                if self.world is not None:
                    await self.store.update_outcomes(
                        self.world["allocations"], self.world["instance"]["tick"]
                    )
            else:
                await self._alert(
                    "database",
                    "database_unavailable",
                    "warning",
                    "Decision history database unavailable: decisions still work, audit "
                    "records are buffered and flushed on recovery",
                )
            await asyncio.sleep(FULL_REFRESH_S)

    async def _alert(
        self, source: str, kind: str, severity: str, message: str, details: Any = None
    ) -> None:
        if self.store is not None:
            await self.store.raise_alert(source, kind, severity, message, details)

    async def _alert_resolve(self, source: str, kind: str) -> None:
        if self.store is not None:
            await self.store.resolve_alert(source, kind)

    async def _restore_cache(self) -> None:
        """Last good world from Redis, so a restarted core-api has cached state at once."""
        if not self.redis_url or self.world is not None:
            return
        try:
            from redis.asyncio import Redis

            async with Redis.from_url(self.redis_url, socket_timeout=1) as r:
                raw = await r.get(CACHE_KEY)
        except Exception as exc:  # noqa: BLE001 - the cache is best effort
            log.warning("cache.restore_failed", error=repr(exc))
            return
        if raw:
            cached = json.loads(raw)
            self.world, self.fetched_at = cached["world"], cached["fetched_at"]
            self.restored_from_cache = True
            log.info("cache.restored", tick=self.world["instance"]["tick"])

    async def _save_cache(self) -> None:
        if not self.redis_url or self.world is None:
            return
        try:
            from redis.asyncio import Redis

            async with Redis.from_url(self.redis_url, socket_timeout=1) as r:
                payload = json.dumps({"world": self.world, "fetched_at": self.fetched_at})
                await r.set(CACHE_KEY, payload)
        except Exception as exc:  # noqa: BLE001 - the cache is best effort
            log.warning("cache.save_failed", error=repr(exc))

    async def poll_once(self) -> None:
        instance = await self._get("/v1/instance")
        fresh = time.time() - self._full_at < FULL_REFRESH_S
        if self.world is not None and instance["tick"] == self._tick and fresh:
            self.world["instance"] = instance
            self.fetched_at, self.last_error = time.time(), None
            return
        (
            regions,
            stations,
            depots,
            routes,
            events,
            metrics,
            allocations,
            supply,
        ) = await asyncio.gather(
            self._get("/v1/regions"),
            self._get("/v1/stations"),
            self._get("/v1/depots"),
            self._get("/v1/routes"),
            self._get("/v1/events"),
            self._get("/v1/metrics"),
            self._get("/v1/allocations"),
            self._get("/v1/supply-arrivals"),
        )
        demand = await asyncio.gather(
            *(
                self._get("/v1/demand-history", station_id=s["id"], limit=3 * HISTORY_TICKS)
                for s in stations
            )
        )
        self.world = {
            "instance": instance,
            "regions": regions,
            "stations": stations,
            "depots": depots,
            "routes": routes,
            "events": events,
            "metrics": metrics,
            "allocations": allocations,
            "supply": supply,
            "demand": {s["id"]: rows for s, rows in zip(stations, demand, strict=True)},
        }
        self._tick = instance["tick"]
        self.fetched_at = self._full_at = time.time()
        self.last_error = None
        self.restored_from_cache = False
        if self.invalid is not None:
            self.invalid = None
            await self._alert_resolve("simulator", "invalid_payload")
        await self._save_cache()
        SIM_TICK.set(instance["tick"])
        SERVICE_LEVEL.set(metrics["service_level"])
        UNMET_LITERS.set(metrics["unmet_demand_liters"])
        SERVED_LITERS.set(metrics["served_demand_liters"])
        await self.refresh_predictions()

    # -- prediction service ---------------------------------------------------------------
    def predict_request(self) -> dict[str, Any]:
        assert self.world is not None
        w = self.world
        tick = w["instance"]["tick"]
        incoming = [
            {"station_id": s, "fuel_type": f, "arrival_tick": eta, "quantity": q}
            for (s, f), items in incoming_schedule(w).items()
            for eta, q in items
        ]
        return {
            "tick": tick,
            "tick_minutes": w["instance"]["tick_minutes"],
            "sim_time": w["instance"]["sim_time"],
            "horizon_ticks": 96,
            "regions": {r["id"]: r["demand_factor"] for r in w["regions"]},
            "stations": w["stations"],
            "demand": [row for rows in w["demand"].values() for row in rows],
            "incoming": incoming,
            "events": [e for e in w["events"] if e["status"] in {"ACTIVE", "SCHEDULED"}],
        }

    async def refresh_predictions(self, *, force: bool = False) -> None:
        if self.intel is None or self.world is None:
            return
        if not force and time.time() < self._predict_retry_at:
            self._set_policy()
            await self._flush_policy_alert()
            return
        started = time.perf_counter()
        try:
            resp = await self.intel.post("/v1/predict", json=self.predict_request())
            resp.raise_for_status()
            self.predictions = resp.json()
        except httpx.HTTPError as exc:
            PREDICT_CALLS.labels("failed").inc()
            self.predict_error = f"{type(exc).__name__}: {exc}"[:200]
            self._predict_retry_at = time.time() + PREDICT_COOLDOWN_S
        else:
            PREDICT_CALLS.labels("ok").inc()
            self.predict_error = None
            self.predict_ok_at = time.time()
            self.predict_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        self._set_policy()
        await self._flush_policy_alert()

    async def _flush_policy_alert(self) -> None:
        if self._pending_alert is None:
            return
        action, message = self._pending_alert
        self._pending_alert = None
        if action == "raise":
            await self._alert("intelligence", "prediction_unavailable", "warning", message)
        else:
            await self._alert_resolve("intelligence", "prediction_unavailable")

    def _predictions_for_now(self) -> dict[str, Any] | None:
        if self.world is None or self.predictions is None or self.predict_error:
            return None
        if self.predictions.get("tick") != self.world["instance"]["tick"]:
            return None
        return self.predictions

    def _set_policy(self) -> None:
        policy = "forecast" if self._predictions_for_now() else "fallback"
        if policy != self._policy:
            if policy == "fallback":
                FALLBACK_ACTIVATIONS.inc()
                log.warning("fallback.activated", reason=self.predict_error or "stale prediction")
                reason = self.predict_error or "no prediction for this tick"
                self._pending_alert = (
                    "raise",
                    f"Prediction service unavailable: fallback allocation policy active ({reason})",
                )
            else:
                log.info("fallback.recovered", model=(self.predictions or {}).get("model_version"))
                self._pending_alert = ("resolve", "")
            self._policy = policy
        POLICY_FORECAST.set(1 if policy == "forecast" else 0)

    # -- views ----------------------------------------------------------------------------
    def meta(self) -> dict[str, Any]:
        now = time.time()
        return {
            "source": "cache" if self.restored_from_cache else "simulator",
            "fetched_at": self.fetched_at,
            "age_s": round(now - self.fetched_at, 1) if self.fetched_at else None,
            "simulator_available": self.last_error is None,
            "error": self.last_error,
            "invalid_payload": self.invalid,
            "sim_stale": self.sim_stale,
            "prediction_service": {
                "available": self.predict_error is None and self.predict_ok_at is not None,
                "error": self.predict_error,
                "latency_ms": self.predict_latency_ms,
                "age_s": round(now - self.predict_ok_at, 1) if self.predict_ok_at else None,
            },
            "database": self.store.status() if self.store else None,
            "chaos": {"corrupting_simulator": self.corruption.active()},
        }

    def view(self) -> dict[str, Any]:
        meta = self.meta()
        if self.world is None:
            return {"meta": meta, "ready": False}
        out = plan(self.world, self._predictions_for_now())
        self._track_shortages(out["stations"])
        self._review_count = sum(1 for r in out["recommendations"] if r["review_required"])
        RECOMMENDATIONS.labels("approvable").set(len(out["recommendations"]))
        RECOMMENDATIONS.labels("next_tick").set(len(out["waiting"]))
        REVIEW_REQUIRED.set(self._review_count)
        return {"meta": meta, "ready": True, **out}

    def _track_shortages(self, stations: list[dict[str, Any]]) -> None:
        now_critical = {
            (s["id"], f["fuel"]) for s in stations for f in s["fuels"] if f["risk"] == "CRITICAL"
        }
        for station, fuel in now_critical - self._critical:
            SHORTAGE_ALERTS.labels(fuel).inc()
            log.warning("shortage.alert", station=station, fuel=fuel, tick=self._tick)
        self._critical = now_critical

    async def review_alerts(self) -> None:
        """Brief §11: prediction confidence too low -> human review requested."""
        while True:
            count = getattr(self, "_review_count", 0)
            if count:
                await self._alert(
                    "decision",
                    "human_review_requested",
                    "info",
                    f"{count} recommendation(s) need human review (confidence below 60 %)",
                )
            else:
                await self._alert_resolve("decision", "human_review_requested")
            await asyncio.sleep(FULL_REFRESH_S)

    def system_status(self) -> dict[str, Any]:
        """Brief §15: health of every important component, plus p95 latency and error rate."""
        m = self.meta()
        pred = m["prediction_service"]
        db = m["database"] or {"available": False, "error": "not configured", "buffered_writes": 0}
        if m["invalid_payload"]:
            sim = ("Degraded", "invalid data rejected; using last valid snapshot")
        elif not m["simulator_available"]:
            sim = ("Down", m["error"] or "unreachable")
        elif m["sim_stale"]:
            sim = ("Degraded", "simulator flags its data as stale")
        else:
            sim = ("Healthy", f"tick {self._tick}, data {m['age_s']} s old")
        if pred["available"]:
            slow = (pred["latency_ms"] or 0) > 500
            pred_state = (
                "Degraded" if slow else "Healthy",
                f"{pred['latency_ms']} ms per forecast",
            )
        else:
            pred_state = ("Down", pred["error"] or "no successful prediction yet")
        if self.world is None:
            engine = ("Down", "no world state yet")
        elif self._policy == "fallback":
            engine = ("Degraded", "fallback allocation policy (prediction service unavailable)")
        elif not m["simulator_available"]:
            engine = ("Degraded", "deciding on cached state")
        else:
            engine = ("Healthy", "forecast policy")
        if db["available"]:
            db_state = (
                ("Degraded", f"{db['buffered_writes']} buffered writes")
                if db["buffered_writes"]
                else ("Healthy", "decision history and alerts persisted")
            )
        else:
            db_state = ("Down", "audit records buffered in memory: " + (db["error"] or ""))
        p95, err, n = REQUEST_WINDOW.summary()
        api = ("Degraded", f"error rate {err:.1%}") if err > 0.05 else ("Healthy", f"{n} requests")
        components = [
            ("Backend API", api),
            ("Database", db_state),
            ("Fuel Simulator", sim),
            ("Prediction Service", pred_state),
            ("Decision Engine", engine),
        ]
        return {
            "components": [{"name": k, "state": s, "detail": d} for k, (s, d) in components],
            "p95_latency_ms": p95,
            "error_rate": round(err, 4),
            "window_s": REQUEST_WINDOW.window_s,
            "open_alerts": self.store.open_alerts() if self.store else [],
        }

    def resilience(self) -> dict[str, Any]:
        """Live state of the brief §11 failure table."""
        m = self.meta()
        return {
            "rows": [
                {
                    "condition": "ML model unavailable",
                    "response": "Fallback allocation policy",
                    "active": self._policy == "fallback",
                    "detail": m["prediction_service"]["error"],
                },
                {
                    "condition": "Invalid simulator response",
                    "response": "Reject input + raise alert",
                    "active": m["invalid_payload"] is not None,
                    "detail": (m["invalid_payload"] or {}).get("errors", [None])[0],
                },
                {
                    "condition": "Prediction confidence too low",
                    "response": "Human review requested",
                    "active": getattr(self, "_review_count", 0) > 0,
                    "detail": f"{getattr(self, '_review_count', 0)} recommendation(s) need review",
                },
                {
                    "condition": "Backend dependency unavailable",
                    "response": "Retry / cached state / degraded mode",
                    "active": (not m["simulator_available"] and m["invalid_payload"] is None)
                    or not (m["database"] or {}).get("available", False),
                    "detail": m["error"] or (m["database"] or {}).get("error"),
                },
            ]
        }

    async def compute(self) -> dict[str, Any]:
        """Fresh end-to-end decision: predict for the current world, then plan (load-test path)."""
        await self.refresh_predictions(force=True)
        return self.view()

    async def approve(
        self, rec_id: str, reviewed: bool = False, user: str = "operator", role: str = "operator"
    ) -> dict[str, Any]:
        rec = next((r for r in self.view().get("recommendations", []) if r["id"] == rec_id), None)
        if rec is None:
            APPROVALS.labels("expired").inc()
            raise HTTPException(404, "recommendation changed or expired; refresh")
        if rec["review_required"] and not reviewed:
            APPROVALS.labels("review_required").inc()
            raise HTTPException(
                409,
                {
                    "code": "REVIEW_REQUIRED",
                    "message": "low-confidence prediction: confirm review",
                    "reasons": rec["review_reasons"],
                },
            )
        body = {
            "idempotency_key": f"jalani-{rec_id}",
            "source_depot_id": rec["depot_id"],
            "destination_station_id": rec["station_id"],
            "route_id": rec["route_id"],
            "fuel_type": rec["fuel"],
            "quantity": rec["quantity"],
        }
        audit = {
            "sim_tick": self._tick or 0,
            "recommendation_id": rec_id,
            "station_id": rec["station_id"],
            "fuel": rec["fuel"],
            "depot_id": rec["depot_id"],
            "route_id": rec["route_id"],
            "quantity": rec["quantity"],
            "policy": rec["policy"],
            "confidence": rec["confidence"],
            "review_required": rec["review_required"],
            "reviewed": reviewed,
            "decided_by": user,
            "role": role,
            "risk_before": rec["impact"]["before"],
            "risk_after": rec["impact"]["after"],
        }
        try:
            resp = await self._call("POST", "/v1/allocations", json=body)  # key makes retry safe
        except SimUnavailable as exc:
            APPROVALS.labels("unavailable").inc()
            log.warning("decision.failed", rec_id=rec_id, error=str(exc))
            if self.store:
                await self.store.record_decision(
                    {**audit, "result": "unavailable", "simulator_status": None,
                     "simulator_detail": {"error": str(exc)}, "allocation_id": None}
                )  # fmt: skip
            raise HTTPException(503, f"simulator unavailable: {exc}") from None
        if resp.status_code not in (200, 201):
            APPROVALS.labels("rejected").inc()
            detail = resp.json().get("detail", resp.text)
            log.warning("decision.rejected", rec_id=rec_id, status=resp.status_code)
            if self.store:
                await self.store.record_decision(
                    {**audit, "result": "rejected", "simulator_status": resp.status_code,
                     "simulator_detail": detail, "allocation_id": None}
                )  # fmt: skip
            raise HTTPException(resp.status_code, detail)
        APPROVALS.labels("accepted").inc()
        allocation = resp.json()
        log.info(
            "decision.approved",
            rec_id=rec_id,
            allocation_id=allocation["id"],
            station=rec["station_id"],
            fuel=rec["fuel"],
            quantity=rec["quantity"],
            policy=rec["policy"],
            confidence=rec["confidence"],
            reviewed=reviewed,
            user=user,
        )
        if self.store:
            await self.store.record_decision(
                {**audit, "result": "accepted", "simulator_status": resp.status_code,
                 "simulator_detail": None, "allocation_id": allocation["id"],
                 "outcome": allocation["status"]}
            )  # fmt: skip
        self._full_at = 0.0  # refresh now so the allocation (and freed capacity) shows at once
        return {"allocation": allocation, "request": body, "recommendation": rec}


class RequestWindow:
    """Rolling window of API request latencies and outcomes for the status panel."""

    def __init__(self, window_s: float = 300.0) -> None:
        self.window_s = window_s
        self.items: deque[tuple[float, float, bool]] = deque(maxlen=50_000)

    def add(self, seconds: float, error: bool) -> None:
        self.items.append((time.time(), seconds, error))

    def summary(self) -> tuple[float | None, float, int]:
        cutoff = time.time() - self.window_s
        while self.items and self.items[0][0] < cutoff:
            self.items.popleft()
        if not self.items:
            return None, 0.0, 0
        durations = sorted(d for _, d, _ in self.items)
        p95 = durations[min(len(durations) - 1, int(0.95 * len(durations)))]
        errors = sum(1 for *_, e in self.items if e)
        return round(p95 * 1000, 1), errors / len(self.items), len(self.items)


REQUEST_WINDOW = RequestWindow()


def router(state: Slice, auth: Auth, *, chaos_enabled: bool = False) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    operator = auth.require("operator")
    admin = auth.require("admin")

    @r.get("/state")
    async def get_state() -> dict[str, Any]:
        return state.view()

    @r.get("/system/status")
    async def system_status() -> dict[str, Any]:
        return state.system_status()

    @r.get("/resilience")
    async def resilience() -> dict[str, Any]:
        return state.resilience()

    @r.get("/alerts")
    async def alerts(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        store = state.store
        return {
            "open": store.open_alerts() if store else [],
            "recent": store.recent_alerts(limit) if store else [],
        }

    @r.get("/decisions")
    async def decisions(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        return {"decisions": state.store.recent_decisions(limit) if state.store else []}

    @r.post("/recommendations/compute")
    async def compute() -> dict[str, Any]:
        return await state.compute()

    @r.post("/recommendations/{rec_id}/approve")
    async def approve(
        rec_id: str = Path(pattern=r"^[A-Za-z0-9_.\-]{1,120}$"),
        body: ApproveBody | None = None,
        user: User = operator,
    ) -> dict[str, Any]:
        reviewed = bool(body and body.reviewed)
        return await state.approve(rec_id, reviewed=reviewed, user=user.username, role=user.role)

    if chaos_enabled:
        chaos_deps = [admin]

        @r.post("/chaos/simulator-fault", dependencies=chaos_deps)
        async def sim_fault(body: chaos.FaultBody) -> dict[str, Any]:
            return await chaos.simulator_fault(state.client, body)

        @r.post("/chaos/demand-spike", dependencies=chaos_deps)
        async def spike(body: chaos.SpikeBody) -> dict[str, Any]:
            return await chaos.demand_spike(state.client, body)

        @r.post("/chaos/corrupt-simulator", dependencies=chaos_deps)
        async def corrupt(body: chaos.OutageBody) -> dict[str, Any]:
            state.corruption.until = time.time() + body.seconds
            log.warning("chaos.corrupt_simulator", seconds=body.seconds)
            return {"corrupting_for_s": body.seconds}

        @r.post("/chaos/prediction-outage", dependencies=chaos_deps)
        async def prediction_outage(body: chaos.OutageBody) -> dict[str, Any]:
            if state.intel is None:
                raise HTTPException(409, "no prediction service configured")
            resp = await state.intel.post("/chaos/outage", json={"seconds": body.seconds})
            return {"status": resp.status_code, "intelligence": resp.json()}

        @r.post("/chaos/clear", dependencies=chaos_deps)
        async def clear() -> dict[str, Any]:
            state.corruption.until = 0.0
            out: dict[str, Any] = {"simulator": await chaos.clear_simulator_faults(state.client)}
            if state.intel is not None:
                try:
                    out["intelligence"] = (await state.intel.post("/chaos/clear")).json()
                except httpx.HTTPError as exc:
                    out["intelligence"] = f"unreachable: {exc}"
            log.info("chaos.cleared")
            return out

    return r


def lifespan_for(state: Slice) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        tasks = [
            asyncio.create_task(state.poll_forever()),
            asyncio.create_task(state.maintain_store()),
            asyncio.create_task(state.review_alerts()),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await state.client.aclose()
            if state.intel is not None:
                await state.intel.aclose()
            if state.store is not None:
                await state.store.close()

    return _lifespan


__all__ = ["FUELS", "REQUEST_WINDOW", "Slice", "lifespan_for", "router"]
