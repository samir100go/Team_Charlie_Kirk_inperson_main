"""POST /v1/predict: forecasts, stockout risk and demand anomalies for every station x fuel."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Literal

from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel, Field

from intelligence.forecast.model import (
    MODEL_VERSION,
    PROFILE_NOISE,
    base_per_tick,
    estimate_level,
    event_level_factors,
    forecast_curve,
    parse_sim_time,
)
from jalani_common.projection import Z90, project

Fuel = Literal["DIESEL", "PETROL", "OCTANE"]
FUELS: tuple[Fuel, ...] = ("DIESEL", "PETROL", "OCTANE")

PREDICTIONS = Counter("jalani_predictions_total", "Station x fuel forecasts produced.")
FORECAST_APE = Histogram(
    "jalani_forecast_abs_pct_error",
    "1-tick-ahead absolute percentage error of the P50 demand forecast.",
    buckets=(0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 2.0),
)
FORECAST_MAPE = Gauge("jalani_forecast_mape", "Rolling MAPE of 1-tick-ahead forecasts (last day).")
CONFIDENCE = Histogram(
    "jalani_prediction_confidence",
    "Model confidence per station x fuel prediction.",
    buckets=(0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)
CONFIDENCE_MEAN = Gauge("jalani_prediction_confidence_mean", "Mean confidence, last predict call.")
ANOMALIES = Gauge("jalani_demand_anomalies", "Station x fuel series flagged as abnormal demand.")


class StationIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    region_id: str = Field(min_length=1, max_length=64)
    demand_profile: str = Field(min_length=1, max_length=32)
    demand_multiplier: float = Field(ge=0)
    status: str
    inventory: dict[Fuel, float]
    capacity: dict[Fuel, float]


class RowIn(BaseModel):
    station_id: str
    fuel_type: Fuel
    tick: int = Field(ge=0)
    sim_time: str
    demand_liters: float = Field(ge=0)


class IncomingIn(BaseModel):
    station_id: str
    fuel_type: Fuel
    arrival_tick: int = Field(ge=0)
    quantity: float = Field(gt=0)


class EventIn(BaseModel):
    id: int
    type: str
    start_tick: int
    end_tick: int
    status: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class PredictRequest(BaseModel):
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0, le=240)
    sim_time: str
    horizon_ticks: int = Field(default=96, ge=4, le=384)
    regions: dict[str, float]
    stations: list[StationIn] = Field(max_length=64)
    demand: list[RowIn] = Field(max_length=20000)
    incoming: list[IncomingIn] = Field(default_factory=list, max_length=2000)
    events: list[EventIn] = Field(default_factory=list, max_length=500)


class ErrorTracker:
    """Scores each 1-tick-ahead P50 forecast once the actual demand row for that tick arrives."""

    def __init__(self, keep: int = 96 * 12) -> None:
        self.pending: dict[tuple[str, str, int], float] = {}
        self.errors: deque[float] = deque(maxlen=keep)

    def score(self, rows: list[RowIn]) -> None:
        for r in rows:
            key = (r.station_id, r.fuel_type, r.tick)
            forecast = self.pending.pop(key, None)
            if forecast is not None and r.demand_liters > 0:
                ape = abs(r.demand_liters - forecast) / r.demand_liters
                self.errors.append(ape)
                FORECAST_APE.observe(ape)
        if self.errors:
            FORECAST_MAPE.set(sum(self.errors) / len(self.errors))
        if len(self.pending) > 5000:  # forecasts for ticks that never came (reset)
            self.pending.clear()

    def remember(self, station: str, fuel: str, tick: int, p50: float) -> None:
        self.pending[(station, fuel, tick)] = p50

    def mape(self) -> float | None:
        return round(sum(self.errors) / len(self.errors), 4) if self.errors else None


TRACKER = ErrorTracker()


def _outage_serving(events: list[EventIn], station: StationIn, ticks: list[int]) -> list[bool]:
    serving = [True] * len(ticks)
    for ev in events:
        if ev.type != "station_outage" or ev.status not in {"ACTIVE", "SCHEDULED"}:
            continue
        ids = ev.parameters.get("station_ids") or []
        if ids and station.id not in ids:
            continue
        for i, t in enumerate(ticks):
            if (ev.status == "ACTIVE" and t <= ev.end_tick) or (
                ev.status == "SCHEDULED" and ev.start_tick <= t <= ev.end_tick
            ):
                serving[i] = False
    if station.status != "OPEN" and not any(
        e.type == "station_outage" and e.status == "ACTIVE" for e in events
    ):
        serving[0] = False  # outage with no visible event: assume at least this tick
    return serving


def predict(req: PredictRequest) -> dict[str, Any]:
    started = time.perf_counter()
    TRACKER.score(req.demand)
    tick_h = req.tick_minutes / 60
    start_time = parse_sim_time(req.sim_time)
    events = [e.model_dump() for e in req.events]

    rows: dict[tuple[str, str], list[RowIn]] = {}
    for r in req.demand:
        rows.setdefault((r.station_id, r.fuel_type), []).append(r)
    incoming: dict[tuple[str, str], list[IncomingIn]] = {}
    for a in req.incoming:
        incoming.setdefault((a.station_id, a.fuel_type), []).append(a)

    out: list[dict[str, Any]] = []
    for st in req.stations:
        region_factor = req.regions.get(st.region_id, 1.0)
        noise = PROFILE_NOISE.get(st.demand_profile, 0.12)
        future = list(range(req.tick, req.tick + req.horizon_ticks))
        serving = _outage_serving(req.events, st, future)
        for fuel in FUELS:
            series = sorted(rows.get((st.id, fuel), []), key=lambda r: r.tick)
            ratios = []
            for r in series:
                base = base_per_tick(
                    st.demand_profile, fuel, region_factor, parse_sim_time(r.sim_time).hour
                )
                if base > 0:
                    ratios.append((r.tick, r.demand_liters / base))
            est = estimate_level(ratios, noise)
            factors = event_level_factors(events, st.id, st.region_id, future)
            mean, sd, _ = forecast_curve(
                profile=st.demand_profile,
                fuel=fuel,
                region_factor=region_factor,
                estimate=est,
                start_tick=req.tick,
                start_time=start_time,
                tick_minutes=req.tick_minutes,
                horizon=req.horizon_ticks,
                level_factors=factors,
            )
            arrivals = [0.0] * req.horizon_ticks
            incoming_total = 0.0
            for a in incoming.get((st.id, fuel), []):
                k = max(0, a.arrival_tick - req.tick)
                if k < req.horizon_ticks:
                    arrivals[k] += a.quantity
                    incoming_total += a.quantity
            proj = project(
                inventory=st.inventory[fuel],
                capacity=st.capacity[fuel],
                demand_mean=mean,
                demand_sd=sd,
                arrivals=arrivals,
                serving=serving,
                tick_hours=tick_h,
            )
            day = min(req.horizon_ticks, round(24 / tick_h))
            d_mean = sum(mean[:day])
            d_sd = math.sqrt(sum(x * x for x in sd[:day]))
            next4 = mean[:4]
            rate_lph = sum(next4) / len(next4) / tick_h if next4 else 0.0
            recent = series[-4:]
            observed_lph = (
                sum(r.demand_liters for r in recent) / len(recent) / tick_h if recent else None
            )
            TRACKER.remember(st.id, fuel, req.tick, mean[0])
            PREDICTIONS.inc()
            CONFIDENCE.observe(est.confidence)
            out.append(
                {
                    "station_id": st.id,
                    "fuel": fuel,
                    "level": round(est.level, 3),
                    "level_se": round(est.level_se, 4),
                    "history_ticks": est.n,
                    "rate_lph_p50": round(rate_lph, 1),
                    "observed_lph": None if observed_lph is None else round(observed_lph, 1),
                    "demand_24h": {
                        "p10": round(max(0.0, d_mean - Z90 * d_sd), 1),
                        "p50": round(d_mean, 1),
                        "p90": round(d_mean + Z90 * d_sd, 1),
                    },
                    "curve_mean": [round(x, 2) for x in mean],
                    "curve_sd": [round(x, 3) for x in sd],
                    "serving": serving,
                    "incoming_l": incoming_total,
                    "stockout": proj.as_dict(),
                    "anomaly": est.anomaly.__dict__,
                    "confidence": est.confidence,
                    "confidence_reasons": est.confidence_reasons,
                    "event_adjusted": any(f != 1.0 for f in factors),
                }
            )
    ANOMALIES.set(sum(1 for p in out if p["anomaly"]["detected"]))
    if out:
        CONFIDENCE_MEAN.set(sum(p["confidence"] for p in out) / len(out))
    return {
        "model_version": MODEL_VERSION,
        "tick": req.tick,
        "horizon_ticks": req.horizon_ticks,
        "tick_minutes": req.tick_minutes,
        "forecast_mape": TRACKER.mape(),
        "compute_ms": round((time.perf_counter() - started) * 1000, 1),
        "predictions": out,
    }
