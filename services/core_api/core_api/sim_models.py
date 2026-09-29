"""Validation of every simulator payload core-api uses (brief §11: invalid response -> reject).

Required fields and enum values are strict; unknown extra fields are tolerated (a newer
simulator may add some). Numbers accept int or float (SIMULATOR_NOTES: inventories start as
ints and become floats). Anything else raises InvalidSimulatorPayload, and the whole refresh
is rejected so decisions never run on a half-valid world.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

Fuel = Literal["DIESEL", "PETROL", "OCTANE"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FuelAmounts(_Model):
    DIESEL: float = Field(ge=0)
    PETROL: float = Field(ge=0)
    OCTANE: float = Field(ge=0)


class Instance(_Model):
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0)
    sim_time: str = Field(min_length=10)
    status: Literal["PAUSED", "RUNNING"]


class Region(_Model):
    id: str = Field(min_length=1)
    demand_factor: float = Field(gt=0)


class Station(_Model):
    id: str = Field(min_length=1)
    name: str
    region_id: str
    status: Literal["OPEN", "OUTAGE"]
    demand_profile: str
    demand_multiplier: float = Field(ge=0)
    capacity: FuelAmounts
    inventory: FuelAmounts


class Depot(_Model):
    id: str = Field(min_length=1)
    name: str
    region_id: str
    status: Literal["OPEN", "CONSTRAINED"]
    dispatch_capacity_per_tick: float = Field(ge=0)
    capacity: FuelAmounts
    inventory: FuelAmounts


class Route(_Model):
    id: str = Field(min_length=1)
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=1)
    max_shipment: float = Field(gt=0)
    status: Literal["AVAILABLE", "DISRUPTED"]


class Event(_Model):
    id: int
    type: str
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class Allocation(_Model):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: Fuel
    quantity: float = Field(gt=0)
    created_tick: int = Field(ge=0)
    expected_arrival_tick: int | None = None
    status: Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"]
    failure_reason: str | None = None


class Metrics(_Model):
    served_demand_liters: float = Field(ge=0)
    unmet_demand_liters: float = Field(ge=0)
    service_level: float = Field(ge=0, le=1)


class DemandRow(_Model):
    station_id: str
    fuel_type: Fuel
    tick: int = Field(ge=0)
    sim_time: str
    demand_liters: float = Field(ge=0)


class SupplyArrival(_Model):
    id: str
    depot_id: str
    fuel_type: Fuel
    quantity: float = Field(ge=0)
    planned_tick: int = Field(ge=0)
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED"]


SCHEMAS: dict[str, TypeAdapter[Any]] = {
    "/v1/instance": TypeAdapter(Instance),
    "/v1/regions": TypeAdapter(list[Region]),
    "/v1/stations": TypeAdapter(list[Station]),
    "/v1/depots": TypeAdapter(list[Depot]),
    "/v1/routes": TypeAdapter(list[Route]),
    "/v1/events": TypeAdapter(list[Event]),
    "/v1/allocations": TypeAdapter(list[Allocation]),
    "/v1/metrics": TypeAdapter(Metrics),
    "/v1/demand-history": TypeAdapter(list[DemandRow]),
    "/v1/supply-arrivals": TypeAdapter(list[SupplyArrival]),
}


class InvalidSimulatorPayload(Exception):
    def __init__(self, endpoint: str, errors: list[str]) -> None:
        self.endpoint = endpoint
        self.errors = errors
        super().__init__(f"invalid payload from {endpoint}: {'; '.join(errors[:3])}")


def validate(endpoint: str, payload: Any) -> Any:
    """Validate a decoded simulator response; return it unchanged (raw dicts) when valid."""
    adapter = SCHEMAS.get(endpoint)
    if adapter is None:
        return payload
    try:
        adapter.validate_python(payload)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']} (got {e.get('input')!r})"[:160]
            for e in exc.errors(include_url=False)
        ]
        raise InvalidSimulatorPayload(endpoint, errors) from None
    return payload
