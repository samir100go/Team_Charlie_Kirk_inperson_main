"""Demo triggers for the brief §11 failure table (disabled unless CHAOS_ENABLED=true).

Each trigger exercises a real failure path, it does not fake the outcome:
- simulator-fault / demand-spike: the simulator's own /admin API (fault injection, events);
- corrupt-simulator: corrupts responses at core-api's integration boundary, so the
  validation layer sees an invalid payload exactly as if the simulator sent it;
- prediction-outage: asks the intelligence service to fail its /v1/predict endpoint, so
  core-api hits a real 503 and falls back.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger()


class Corruption:
    def __init__(self) -> None:
        self.until = 0.0

    def active(self) -> bool:
        return time.time() < self.until

    def apply(self, path: str, payload: Any) -> Any:
        """Break one field of /v1/stations the way a buggy upstream would."""
        if not self.active() or path != "/v1/stations" or not isinstance(payload, list):
            return payload
        broken = [dict(s) for s in payload]
        if broken:
            inv = dict(broken[0].get("inventory") or {})
            inv["DIESEL"] = "N/A"  # a string where a number is required
            broken[0]["inventory"] = inv
            broken[0]["status"] = "ON_FIRE"  # an enum value the contract does not allow
        return broken


class FaultBody(BaseModel):
    type: Literal["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"]
    seconds: int = Field(default=60, gt=0, le=600)
    rate: float | None = Field(default=None, gt=0, le=1)
    delay_ms: int | None = Field(default=None, gt=0, le=10_000)


class SpikeBody(BaseModel):
    region_id: Literal["region-dhaka", "region-chattogram"] = "region-dhaka"
    multiplier: float = Field(default=3.0, ge=1.1, le=10)
    ticks: int = Field(default=48, gt=0, le=400)


class OutageBody(BaseModel):
    seconds: int = Field(default=60, gt=0, le=600)


async def simulator_fault(sim: httpx.AsyncClient, body: FaultBody) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if body.rate is not None:
        params["rate"] = body.rate
    if body.delay_ms is not None:
        params["delay_ms"] = body.delay_ms
    resp = await sim.post(
        "/admin/faults",
        json={"type": body.type, "duration_seconds": body.seconds, "parameters": params},
    )
    log.warning("chaos.simulator_fault", type=body.type, seconds=body.seconds, params=params)
    return {"status": resp.status_code, "fault": resp.json()}


async def demand_spike(sim: httpx.AsyncClient, body: SpikeBody) -> dict[str, Any]:
    tick = (await sim.get("/v1/instance")).json()["tick"]
    resp = await sim.post(
        "/admin/events",
        json={
            "type": "demand_spike",
            "start_tick": tick,
            "duration_ticks": body.ticks,
            "parameters": {"region_ids": [body.region_id], "multiplier": body.multiplier},
        },
    )
    log.warning("chaos.demand_spike", region=body.region_id, multiplier=body.multiplier, tick=tick)
    return {"status": resp.status_code, "event": resp.json()}


async def clear_simulator_faults(sim: httpx.AsyncClient) -> Any:
    return (await sim.post("/admin/faults/clear")).json()
