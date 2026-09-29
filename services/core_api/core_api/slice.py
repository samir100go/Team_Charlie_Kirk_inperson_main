"""Thin demo slice: poll the simulator, keep the last good world in memory, recommend refills.

- Polls /v1/instance every POLL_S; on a new tick (or every FULL_REFRESH_S) refreshes
  stations, depots, routes, events, metrics, allocations and recent demand-history.
  Every call: timeout + one retry. If any call fails the whole refresh is dropped and
  the last good state is kept.
- `plan()` (pure) is the decision rule: hours to stockout = inventory / recent demand rate.
  Below RECOMMEND_BELOW_H of cover, refill from the fastest AVAILABLE route. Candidates are
  served most-urgent first against each depot's remaining dispatch capacity for this tick
  (dispatch_capacity_per_tick minus PENDING allocations, which were all created this tick),
  its inventory, and the station's room (capacity - inventory - fuel in flight: the
  simulator destroys overflow, SIMULATOR_NOTES #3/#4). What doesn't fit waits for next tick.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from prometheus_client import Counter, Gauge

POLL_S = 0.5
FULL_REFRESH_S = 2.0  # also catches /admin/reset (tick back to 0) and new allocations
TIMEOUT = httpx.Timeout(2.0, connect=1.0)
RATE_WINDOW_TICKS = 8
RECOMMEND_BELOW_H = 24.0
CRITICAL_H, HIGH_H = 8.0, 16.0
MIN_SHIPMENT_L = 500.0
FUELS = ("DIESEL", "PETROL", "OCTANE")
IN_FLIGHT = {"PENDING", "IN_TRANSIT"}

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


class SimUnavailable(Exception):
    pass


def _endpoint(path: str) -> str:
    return "/v1/allocations/{id}/cancel" if path.endswith("/cancel") else path


class Slice:
    def __init__(self, simulator_url: str) -> None:
        self.client = httpx.AsyncClient(base_url=simulator_url.rstrip("/"), timeout=TIMEOUT)
        self.world: dict[str, Any] | None = None
        self.fetched_at: float | None = None
        self.last_error: str | None = None
        self.sim_stale = False
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
            try:
                await self.poll_once()
                SIM_AVAILABLE.set(1)
            except (SimUnavailable, httpx.HTTPStatusError) as exc:
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
        stations, depots, routes, events, metrics, allocations = await asyncio.gather(
            self._get("/v1/stations"),
            self._get("/v1/depots"),
            self._get("/v1/routes"),
            self._get("/v1/events"),
            self._get("/v1/metrics"),
            self._get("/v1/allocations"),
        )
        demand = await asyncio.gather(
            *(
                self._get("/v1/demand-history", station_id=s["id"], limit=3 * RATE_WINDOW_TICKS)
                for s in stations
            )
        )
        self.world = {
            "instance": instance,
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

    def view(self) -> dict[str, Any]:
        now = time.time()
        meta = {
            "source": "simulator",
            "fetched_at": self.fetched_at,
            "age_s": round(now - self.fetched_at, 1) if self.fetched_at else None,
            "simulator_available": self.last_error is None,
            "error": self.last_error,
            "sim_stale": self.sim_stale,
        }
        if self.world is None:
            return {"meta": meta, "ready": False}
        out = plan(self.world)
        RECOMMENDATIONS.labels("approvable").set(len(out["recommendations"]))
        RECOMMENDATIONS.labels("next_tick").set(len(out["waiting"]))
        return {"meta": meta, "ready": True, **out}

    async def approve(self, rec_id: str) -> dict[str, Any]:
        rec = next((r for r in self.view().get("recommendations", []) if r["id"] == rec_id), None)
        if rec is None:
            APPROVALS.labels("expired").inc()
            raise HTTPException(404, "recommendation changed or expired; refresh")
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
            raise HTTPException(503, f"simulator unavailable: {exc}") from None
        if resp.status_code not in (200, 201):
            APPROVALS.labels("rejected").inc()
            raise HTTPException(resp.status_code, resp.json().get("detail", resp.text))
        APPROVALS.labels("accepted").inc()
        self._full_at = 0.0  # refresh now so the allocation (and freed capacity) shows at once
        return {"allocation": resp.json(), "request": body}


# -- decision rule (pure) -------------------------------------------------------------------


def demand_rate_per_tick(rows: list[dict[str, Any]], fuel: str) -> float:
    """Mean demand (L/tick) over the newest RATE_WINDOW_TICKS rows for one fuel.

    demand-history is newest-first with one row per station x fuel x tick, so the first
    N rows of a fuel are its last N ticks.
    """
    recent = [r["demand_liters"] for r in rows if r["fuel_type"] == fuel][:RATE_WINDOW_TICKS]
    return statistics.fmean(recent) if recent else 0.0


def risk_tier(hours: float | None, status: str) -> str:
    if status != "OPEN":
        return "OUTAGE"
    if hours is None:
        return "NORMAL"
    if hours < CRITICAL_H:
        return "CRITICAL"
    if hours < HIGH_H:
        return "HIGH"
    if hours < RECOMMEND_BELOW_H:
        return "ELEVATED"
    return "NORMAL"


def plan(w: dict[str, Any]) -> dict[str, Any]:
    tick = w["instance"]["tick"]
    tick_h = w["instance"]["tick_minutes"] / 60
    in_flight: dict[tuple[str, str], float] = {}
    dispatched: dict[str, float] = {}  # PENDING = created this tick = uses this tick's cap
    shipped_now: set[tuple[str, str]] = set()
    for a in w["allocations"]:
        if a["status"] in IN_FLIGHT:
            key = (a["destination_station_id"], a["fuel_type"])
            in_flight[key] = in_flight.get(key, 0.0) + a["quantity"]
        if a["status"] == "PENDING":
            src = a["source_depot_id"]
            dispatched[src] = dispatched.get(src, 0.0) + a["quantity"]
            shipped_now.add((a["destination_station_id"], a["fuel_type"]))
    depots = {d["id"]: d for d in w["depots"]}
    dispatch_left = {
        d["id"]: max(0.0, float(d["dispatch_capacity_per_tick"]) - dispatched.get(d["id"], 0.0))
        for d in w["depots"]
    }
    stock_left = {d["id"]: {f: float(d["inventory"][f]) for f in FUELS} for d in w["depots"]}

    stations_out, candidates = [], []
    for st in w["stations"]:
        fuels = []
        for fuel in FUELS:
            rate_h = demand_rate_per_tick(w["demand"].get(st["id"], []), fuel) / tick_h
            inv, cap = float(st["inventory"][fuel]), float(st["capacity"][fuel])
            flight = in_flight.get((st["id"], fuel), 0.0)
            hours = inv / rate_h if rate_h > 0 else None
            fuels.append(
                {
                    "fuel": fuel,
                    "inventory": round(inv, 1),
                    "capacity": cap,
                    "in_flight": flight,
                    "demand_lph": round(rate_h, 1),
                    "hours_to_stockout": None if hours is None else round(hours, 1),
                    "risk": risk_tier(hours, st["status"]),
                }
            )
            if st["status"] != "OPEN" or rate_h <= 0 or (st["id"], fuel) in shipped_now:
                continue
            if (inv + flight) / rate_h >= RECOMMEND_BELOW_H:
                continue
            candidates.append((hours or 0.0, st, fuel, inv, cap, flight, rate_h))
        stations_out.append(
            {
                "id": st["id"],
                "name": st["name"],
                "status": st["status"],
                "demand_multiplier": st["demand_multiplier"],
                "fuels": fuels,
            }
        )

    recs, waiting = [], []
    for hours, st, fuel, inv, cap, flight, rate_h in sorted(candidates, key=lambda c: c[0]):
        room = cap - inv - flight
        base = {
            "station_id": st["id"],
            "station": st["name"],
            "fuel": fuel,
            "hours_to_stockout": round(hours, 1),
            "demand_lph": round(rate_h, 1),
        }
        if room < MIN_SHIPMENT_L:
            waiting.append({**base, "reason": "tank is full once incoming fuel lands"})
            continue
        routes = sorted(
            (
                r
                for r in w["routes"]
                if r["destination_station_id"] == st["id"] and r["status"] == "AVAILABLE"
            ),
            key=lambda r: r["transit_ticks"],
        )
        chosen = None
        for route in routes:
            src = route["source_depot_id"]
            qty = math.floor(
                min(route["max_shipment"], stock_left[src][fuel], dispatch_left[src], room)
            )
            if qty >= MIN_SHIPMENT_L:
                chosen = (route, depots[src], float(qty))
                break
        if chosen is None:
            why = (
                "no open route to this station"
                if not routes
                else "dispatch capacity this tick is taken by more urgent stations "
                + "(or depot stock is out)"
            )
            waiting.append({**base, "reason": why})
            continue
        route, depot, qty = chosen
        dispatch_left[depot["id"]] -= qty
        stock_left[depot["id"]][fuel] -= qty
        cover_after = (inv + flight + qty) / rate_h
        recs.append(
            {
                **base,
                "id": f"t{tick}-{st['id']}-{fuel}-{depot['id']}-{int(qty)}",
                "depot_id": depot["id"],
                "depot": depot["name"],
                "route_id": route["id"],
                "transit_ticks": route["transit_ticks"],
                "quantity": qty,
                "current_l": round(inv, 1),
                "incoming_l": flight,
                "hours_cover_after": round(cover_after, 1),
                "reason": (
                    f"{st['name']} {fuel} runs out in ~{hours:.1f} h at {rate_h:.0f} L/h. "
                    f"Ship {qty:.0f} L from {depot['name']} via {route['id']} "
                    f"({route['transit_ticks']} ticks): cover = ({inv:.0f} now + {flight:.0f} "
                    f"incoming + {qty:.0f}) L / {rate_h:.0f} L/h = {cover_after:.1f} h"
                ),
            }
        )
    return {
        "instance": w["instance"],
        "metrics": w["metrics"],
        "events": [e for e in w.get("events", []) if e["status"] in {"ACTIVE", "SCHEDULED"}],
        "stations": stations_out,
        "depots": [
            {
                "id": d["id"],
                "name": d["name"],
                "status": d["status"],
                "inventory": d["inventory"],
                "dispatch_capacity_per_tick": d["dispatch_capacity_per_tick"],
                "dispatch_left": round(dispatch_left[d["id"]], 1),
            }
            for d in w["depots"]
        ],
        "recommendations": recs,
        "waiting": waiting,
        "allocations": w["allocations"][:10],
    }


def router(state: Slice) -> APIRouter:
    r = APIRouter(prefix="/api/v1")

    @r.get("/state")
    async def get_state() -> dict[str, Any]:
        return state.view()

    @r.post("/recommendations/{rec_id}/approve")
    async def approve(rec_id: str) -> dict[str, Any]:
        return await state.approve(rec_id)

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

    return _lifespan
