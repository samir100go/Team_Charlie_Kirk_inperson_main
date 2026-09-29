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
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, FastAPI, HTTPException
from prometheus_client import Counter, Gauge
from pydantic import BaseModel

from core_api.decision import FUELS, incoming_schedule, plan

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


class SimUnavailable(Exception):
    pass


class ApproveBody(BaseModel):
    reviewed: bool = False  # operator confirms they reviewed a low-confidence recommendation


def _endpoint(path: str) -> str:
    return "/v1/allocations/{id}/cancel" if path.endswith("/cancel") else path


class Slice:
    def __init__(self, simulator_url: str, intelligence_url: str | None = None) -> None:
        self.client = httpx.AsyncClient(base_url=simulator_url.rstrip("/"), timeout=TIMEOUT)
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
        return resp.json()

    # -- polling --------------------------------------------------------------------------
    async def poll_forever(self) -> None:
        while True:
            was_down = self.last_error is not None
            try:
                await self.poll_once()
                SIM_AVAILABLE.set(1)
                if was_down:
                    log.info("integration.simulator_recovered", tick=self._tick)
            except (SimUnavailable, httpx.HTTPStatusError) as exc:
                if not was_down:
                    log.warning("integration.simulator_failed", error=str(exc))
                self.last_error = str(exc)
                SIM_AVAILABLE.set(0)
            if self.fetched_at:
                SIM_DATA_AGE.set(time.time() - self.fetched_at)
            await asyncio.sleep(POLL_S)

    async def poll_once(self) -> None:
        instance = await self._get("/v1/instance")
        fresh = time.time() - self._full_at < FULL_REFRESH_S
        if self.world is not None and instance["tick"] == self._tick and fresh:
            self.world["instance"] = instance
            self.fetched_at, self.last_error = time.time(), None
            return
        regions, stations, depots, routes, events, metrics, allocations = await asyncio.gather(
            self._get("/v1/regions"),
            self._get("/v1/stations"),
            self._get("/v1/depots"),
            self._get("/v1/routes"),
            self._get("/v1/events"),
            self._get("/v1/metrics"),
            self._get("/v1/allocations"),
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
            "demand": {s["id"]: rows for s, rows in zip(stations, demand, strict=True)},
        }
        self._tick = instance["tick"]
        self.fetched_at = self._full_at = time.time()
        self.last_error = None
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
            else:
                log.info("fallback.recovered", model=(self.predictions or {}).get("model_version"))
            self._policy = policy
        POLICY_FORECAST.set(1 if policy == "forecast" else 0)

    # -- views ----------------------------------------------------------------------------
    def view(self) -> dict[str, Any]:
        now = time.time()
        meta = {
            "source": "simulator",
            "fetched_at": self.fetched_at,
            "age_s": round(now - self.fetched_at, 1) if self.fetched_at else None,
            "simulator_available": self.last_error is None,
            "error": self.last_error,
            "sim_stale": self.sim_stale,
            "prediction_service": {
                "available": self.predict_error is None and self.predict_ok_at is not None,
                "error": self.predict_error,
                "latency_ms": self.predict_latency_ms,
                "age_s": round(now - self.predict_ok_at, 1) if self.predict_ok_at else None,
            },
        }
        if self.world is None:
            return {"meta": meta, "ready": False}
        out = plan(self.world, self._predictions_for_now())
        self._track_shortages(out["stations"])
        RECOMMENDATIONS.labels("approvable").set(len(out["recommendations"]))
        RECOMMENDATIONS.labels("next_tick").set(len(out["waiting"]))
        REVIEW_REQUIRED.set(sum(1 for r in out["recommendations"] if r["review_required"]))
        return {"meta": meta, "ready": True, **out}

    def _track_shortages(self, stations: list[dict[str, Any]]) -> None:
        now_critical = {
            (s["id"], f["fuel"]) for s in stations for f in s["fuels"] if f["risk"] == "CRITICAL"
        }
        for station, fuel in now_critical - self._critical:
            SHORTAGE_ALERTS.labels(fuel).inc()
            log.warning("shortage.alert", station=station, fuel=fuel, tick=self._tick)
        self._critical = now_critical

    async def compute(self) -> dict[str, Any]:
        """Fresh end-to-end decision: predict for the current world, then plan (load-test path)."""
        await self.refresh_predictions(force=True)
        return self.view()

    async def approve(self, rec_id: str, reviewed: bool = False) -> dict[str, Any]:
        rec = next((r for r in self.view().get("recommendations", []) if r["id"] == rec_id), None)
        if rec is None:
            APPROVALS.labels("expired").inc()
            raise HTTPException(404, "recommendation changed or expired; refresh")
        if rec["review_required"] and not reviewed:
            APPROVALS.labels("review_required").inc()
            raise HTTPException(
                409,
                {"code": "REVIEW_REQUIRED", "message": "low-confidence prediction: confirm review",
                 "reasons": rec["review_reasons"]},
            )  # fmt: skip
        body = {
            "idempotency_key": f"jalani-{rec_id}",
            "source_depot_id": rec["depot_id"],
            "destination_station_id": rec["station_id"],
            "route_id": rec["route_id"],
            "fuel_type": rec["fuel"],
            "quantity": rec["quantity"],
        }
        try:
            resp = await self._call("POST", "/v1/allocations", json=body)  # key makes retry safe
        except SimUnavailable as exc:
            APPROVALS.labels("unavailable").inc()
            log.warning("decision.failed", rec_id=rec_id, error=str(exc))
            raise HTTPException(503, f"simulator unavailable: {exc}") from None
        if resp.status_code not in (200, 201):
            APPROVALS.labels("rejected").inc()
            log.warning("decision.rejected", rec_id=rec_id, status=resp.status_code)
            raise HTTPException(resp.status_code, resp.json().get("detail", resp.text))
        APPROVALS.labels("accepted").inc()
        allocation = resp.json()
        log.info(
            "decision.approved", rec_id=rec_id, allocation_id=allocation["id"],
            station=rec["station_id"], fuel=rec["fuel"], quantity=rec["quantity"],
            policy=rec["policy"], confidence=rec["confidence"], reviewed=reviewed,
        )  # fmt: skip
        self._full_at = 0.0  # refresh now so the allocation (and freed capacity) shows at once
        return {"allocation": allocation, "request": body, "recommendation": rec}


def router(state: Slice) -> APIRouter:
    r = APIRouter(prefix="/api/v1")

    @r.get("/state")
    async def get_state() -> dict[str, Any]:
        return state.view()

    @r.post("/recommendations/compute")
    async def compute() -> dict[str, Any]:
        return await state.compute()

    @r.post("/recommendations/{rec_id}/approve")
    async def approve(rec_id: str, body: ApproveBody | None = None) -> dict[str, Any]:
        return await state.approve(rec_id, reviewed=bool(body and body.reviewed))

    return r


def lifespan_for(state: Slice) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(state.poll_forever())
        try:
            yield
        finally:
            task.cancel()
            await state.client.aclose()
            if state.intel is not None:
                await state.intel.aclose()

    return _lifespan


__all__ = ["FUELS", "Slice", "lifespan_for", "router"]
