"""Thin demo slice: poll the simulator, keep the last good world in memory, recommend refills.

- Polls /v1/instance every POLL_S; on a new tick refreshes stations, depots, routes,
  metrics, allocations and recent demand-history. Every call: timeout + one retry.
  If any call fails the whole refresh is dropped and the last good state is kept.
- Decision rule (explainable): hours to stockout = inventory / recent demand rate.
  Below RECOMMEND_BELOW_H, refill from the fastest AVAILABLE route whose depot has fuel,
  capped by route.max_shipment, depot inventory and station room (capacity - inventory
  - fuel already in flight: the simulator destroys overflow, SIMULATOR_NOTES #3/#4).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException

POLL_S = 0.5
FULL_REFRESH_S = 2.0  # also catches /admin/reset (tick back to 0) and new allocations while paused
TIMEOUT = httpx.Timeout(2.0, connect=1.0)
RATE_WINDOW_TICKS = 8
RECOMMEND_BELOW_H = 24.0
CRITICAL_H, HIGH_H = 8.0, 16.0
MIN_SHIPMENT_L = 500.0
FUELS = ("DIESEL", "PETROL", "OCTANE")
IN_FLIGHT = {"PENDING", "IN_TRANSIT"}


class SimUnavailable(Exception):
    pass


class Slice:
    def __init__(self, simulator_url: str) -> None:
        self.client = httpx.AsyncClient(base_url=simulator_url.rstrip("/"), timeout=TIMEOUT)
        self.world: dict[str, Any] | None = None
        self.fetched_at: float | None = None
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self.sim_stale = False
        self._tick: int | None = None
        self._full_at = 0.0

    # -- simulator calls: timeout + exactly one retry -------------------------------------
    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        last: str = ""
        for _attempt in range(2):
            try:
                resp = await self.client.request(method, path, **kw)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__} on {path}"
                continue
            if resp.status_code >= 500:
                last = f"HTTP {resp.status_code} on {path}"
                continue
            return resp
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
            except (SimUnavailable, httpx.HTTPStatusError) as exc:
                self.last_error, self.last_error_at = str(exc), time.time()
            await asyncio.sleep(POLL_S)

    async def poll_once(self) -> None:
        instance = await self._get("/v1/instance")
        fresh = time.time() - self._full_at < FULL_REFRESH_S
        if self.world is not None and instance["tick"] == self._tick and fresh:
            self.world["instance"] = instance
            self.fetched_at, self.last_error = time.time(), None
            return
        stations, depots, routes, metrics, allocations = await asyncio.gather(
            self._get("/v1/stations"),
            self._get("/v1/depots"),
            self._get("/v1/routes"),
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
            "metrics": metrics,
            "allocations": allocations,
            "demand": {s["id"]: rows for s, rows in zip(stations, demand, strict=True)},
        }
        self._tick = instance["tick"]
        self.fetched_at = self._full_at = time.time()
        self.last_error = None

    # -- decision rule --------------------------------------------------------------------
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
        w = self.world
        tick_h = w["instance"]["tick_minutes"] / 60
        in_flight: dict[tuple[str, str], float] = {}
        for a in w["allocations"]:
            if a["status"] in IN_FLIGHT:
                key = (a["destination_station_id"], a["fuel_type"])
                in_flight[key] = in_flight.get(key, 0.0) + a["quantity"]
        depots = {d["id"]: d for d in w["depots"]}
        stations_out, recs = [], []
        for st in w["stations"]:
            fuels = []
            rows = w["demand"].get(st["id"], [])
            for fuel in FUELS:
                recent = [r["demand_liters"] for r in rows if r["fuel_type"] == fuel]
                rate = statistics.fmean(recent[:RATE_WINDOW_TICKS]) if recent else 0.0
                rate_h = rate / tick_h if tick_h else 0.0
                inv = float(st["inventory"][fuel])
                cap = float(st["capacity"][fuel])
                flight = in_flight.get((st["id"], fuel), 0.0)
                hours = inv / rate_h if rate_h > 0 else None
                risk = _risk(hours, st["status"])
                fuels.append(
                    {
                        "fuel": fuel,
                        "inventory": round(inv, 1),
                        "capacity": cap,
                        "in_flight": flight,
                        "demand_lph": round(rate_h, 1),
                        "hours_to_stockout": None if hours is None else round(hours, 1),
                        "risk": risk,
                    }
                )
                cover_with_flight = (inv + flight) / rate_h if rate_h > 0 else None
                if st["status"] != "OPEN" or cover_with_flight is None:
                    continue
                if cover_with_flight >= RECOMMEND_BELOW_H:
                    continue
                rec = _recommend(st, fuel, inv, cap, flight, rate_h, hours, w["routes"], depots)
                if rec:
                    rec["id"] = f"t{w['instance']['tick']}-{st['id']}-{fuel}"
                    recs.append(rec)
            stations_out.append(
                {"id": st["id"], "name": st["name"], "status": st["status"], "fuels": fuels}
            )
        recs.sort(key=lambda r: r["hours_to_stockout"] if r["hours_to_stockout"] else 0)
        return {
            "meta": meta,
            "ready": True,
            "instance": w["instance"],
            "metrics": w["metrics"],
            "stations": stations_out,
            "depots": [
                {"id": d["id"], "status": d["status"], "inventory": d["inventory"]}
                for d in w["depots"]
            ],
            "recommendations": recs,
            "allocations": w["allocations"][:10],
        }

    async def approve(self, rec_id: str) -> dict[str, Any]:
        rec = next((r for r in self.view().get("recommendations", []) if r["id"] == rec_id), None)
        if rec is None:
            raise HTTPException(404, "recommendation expired or unknown; refresh")
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
            raise HTTPException(503, f"simulator unavailable: {exc}") from None
        if resp.status_code not in (200, 201):
            detail = resp.json().get("detail", resp.text)
            raise HTTPException(resp.status_code, detail)
        self._full_at = 0.0  # force a refresh so the allocation shows up at once
        return {"allocation": resp.json(), "request": body}


def _risk(hours: float | None, status: str) -> str:
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


def _recommend(
    st: dict[str, Any],
    fuel: str,
    inv: float,
    cap: float,
    flight: float,
    rate_h: float,
    hours: float | None,
    routes: list[dict[str, Any]],
    depots: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    room = cap - inv - flight
    options = [
        r
        for r in routes
        if r["destination_station_id"] == st["id"]
        and r["status"] == "AVAILABLE"
        and depots[r["source_depot_id"]]["inventory"][fuel] >= MIN_SHIPMENT_L
    ]
    for route in sorted(options, key=lambda r: r["transit_ticks"]):
        depot = depots[route["source_depot_id"]]
        qty = float(int(min(route["max_shipment"], depot["inventory"][fuel], room)))
        if qty < MIN_SHIPMENT_L:
            continue
        cover_after = (inv + flight + qty) / rate_h
        return {
            "station_id": st["id"],
            "station": st["name"],
            "fuel": fuel,
            "depot_id": depot["id"],
            "route_id": route["id"],
            "quantity": qty,
            "transit_ticks": route["transit_ticks"],
            "hours_to_stockout": None if hours is None else round(hours, 1),
            "hours_cover_after": round(cover_after, 1),
            "reason": (
                f"{st['name']} {fuel} runs out in ~{hours:.1f} h at {rate_h:.0f} L/h; "
                f"ship {qty:.0f} L from {depot['name']} via {route['id']} "
                f"({route['transit_ticks']} ticks) for ~{cover_after:.1f} h of cover"
            ),
        }
    return None


def router(state: Slice) -> APIRouter:
    r = APIRouter(prefix="/api/v1")

    @r.get("/state")
    async def get_state() -> dict[str, Any]:
        return state.view()

    @r.post("/recommendations/{rec_id}/approve")
    async def approve(rec_id: str) -> dict[str, Any]:
        return await state.approve(rec_id)

    return r


def lifespan_for(state: Slice):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(state.poll_forever())
        try:
            yield
        finally:
            task.cancel()
            await state.client.aclose()

    return _lifespan
