"""Demand forecast per station x fuel: structural prior + online level + change-point detection.

demand(t) = level x base(t) x (1 + noise), where
  base(t) = profile_daily / 96 x region.demand_factor x hour_factor(profile, hour(t))
is the documented structure (Integration Guide §8.5-§8.6, verified in Phase 0: hour factors
are NOT normalized, noise is uniform +-noise). `level` is what we learn online: the ratio of
observed to structural demand. It is ~1.0 in normal operations and jumps with demand spikes,
so the model adapts within a few ticks, and a change-point test on the same ratios is the
anomaly detector. Known crisis events (/v1/events) shift the level at their start/end ticks.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

MODEL_VERSION = "structural-level-v1"

# Integration Guide §8.5 (liters per simulated day) and noise; §8.6 hour-of-day factors.
PROFILE_DAILY: dict[str, dict[str, float]] = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}
PROFILE_NOISE = {"urban_high": 0.10, "industrial": 0.08, "highway": 0.12, "regional": 0.10}

WINDOW_TICKS = 32  # how much history the level estimate may use
SPIKE_REL = 0.25  # demand >= 25 % off the documented profile ...
SPIKE_Z = 4.0  # ... by >= 4 standard errors -> anomaly
MIN_TICKS_FULL_CONFIDENCE = 8


def hour_factor(profile: str, hour: int) -> float:
    if profile == "industrial":
        return 1.55 if 6 <= hour <= 17 else 0.45
    if profile == "highway":
        return 1.35 if (6 <= hour <= 9 or 16 <= hour <= 20) else 0.75
    if profile == "urban_high":
        return 1.45 if (7 <= hour <= 9 or 16 <= hour <= 20) else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour <= 20 else 0.65
    return 1.0  # unknown profile: flat shape, the level estimate carries the scale


def base_per_tick(profile: str, fuel: str, region_factor: float, hour: int) -> float:
    daily = PROFILE_DAILY.get(profile, {}).get(fuel)
    if daily is None:
        return 0.0
    return daily / 96 * region_factor * hour_factor(profile, hour)


@dataclass
class Anomaly:
    detected: bool = False
    direction: str | None = None  # "spike" | "drop"
    ratio: float = 1.0  # current level vs the documented profile (1.0 = normal)
    z: float = 0.0  # (level - 1) / level standard error
    since_tick: int | None = None  # first tick of the current regime, if inside the window
    change_ratio: float | None = None  # current regime vs the one before it


@dataclass
class LevelEstimate:
    level: float
    level_se: float
    n: int
    noise: float
    anomaly: Anomaly
    confidence: float
    confidence_reasons: list[str] = field(default_factory=list)


def estimate_level(ratios: Sequence[tuple[int, float]], noise: float) -> LevelEstimate:
    """`ratios` = (tick, observed / structural demand), oldest -> newest.

    Regime detection: simulator noise is uniform +-noise (bounded), so all ticks of one
    regime satisfy max/min <= (1+noise)/(1-noise). The current regime is the longest recent
    run that stays inside that bound; the level is its mean.
    """
    per_tick_sd = noise / math.sqrt(3)
    if not ratios:
        return LevelEstimate(
            level=1.0,
            level_se=0.5,
            n=0,
            noise=noise,
            anomaly=Anomaly(),
            confidence=0.2,
            confidence_reasons=["no demand history yet: structural prior only"],
        )
    window = list(ratios)[-WINDOW_TICKS:]
    values = [max(r, 1e-6) for _, r in window]
    ticks = [t for t, _ in window]

    bound = (1 + noise) / (1 - noise) * 1.02
    s = len(values) - 1
    lo = hi = values[s]
    while s > 0:
        v = values[s - 1]
        if max(hi, v) / min(lo, v) > bound:
            break
        lo, hi = min(lo, v), max(hi, v)
        s -= 1
    used = values[s:]
    n = len(used)
    level = statistics.fmean(used)
    spread = statistics.pstdev(used) if n > 1 else 0.0
    level_se = max(spread, per_tick_sd * level) / math.sqrt(n)

    anomaly = Anomaly(ratio=round(level, 3), z=round((level - 1) / level_se, 2))
    if s > 0:
        prev = values[max(0, s - 8) : s]
        anomaly.change_ratio = round(level / statistics.fmean(prev), 3)
        anomaly.since_tick = ticks[s]
    if abs(level - 1) >= SPIKE_REL and abs(anomaly.z) >= SPIKE_Z:
        anomaly.detected = True
        anomaly.direction = "spike" if level > 1 else "drop"

    reasons: list[str] = []
    rel_se = level_se / level
    confidence = max(0.0, 1 - rel_se / 0.2) * min(1.0, n / MIN_TICKS_FULL_CONFIDENCE)
    if n < MIN_TICKS_FULL_CONFIDENCE:
        if s > 0:
            reasons.append(f"demand regime changed {n} tick(s) ago: level not settled yet")
        else:
            reasons.append(f"only {n} tick(s) of demand history")
    if rel_se > 0.05:
        reasons.append(f"level uncertainty +-{rel_se:.0%}")
    return LevelEstimate(
        level=level,
        level_se=level_se,
        n=n,
        noise=noise,
        anomaly=anomaly,
        confidence=round(min(1.0, confidence), 3),
        confidence_reasons=reasons,
    )


def event_level_factors(
    events: Sequence[dict[str, Any]],
    station_id: str,
    region_id: str,
    future_ticks: Sequence[int],
) -> list[float]:
    """Known demand_spike effects on future ticks, relative to the current level.

    A spike is effective on ticks start..end inclusive (Phase 0: duration + 1 ticks). The
    current level already contains every ACTIVE spike, so an active spike contributes
    1/multiplier after its end, and a SCHEDULED one contributes multiplier during its window.
    """
    factors = [1.0] * len(future_ticks)
    for ev in events:
        if ev.get("type") != "demand_spike" or ev.get("status") not in {"ACTIVE", "SCHEDULED"}:
            continue
        params = ev.get("parameters") or {}
        stations, regions = params.get("station_ids") or [], params.get("region_ids") or []
        if (stations or regions) and station_id not in stations and region_id not in regions:
            continue
        mult = float(params.get("multiplier", 1.5)) or 1.0
        start, end = int(ev["start_tick"]), int(ev["end_tick"])
        for i, t in enumerate(future_ticks):
            if ev["status"] == "ACTIVE" and t > end:
                factors[i] /= mult
            elif ev["status"] == "SCHEDULED" and start <= t <= end:
                factors[i] *= mult
    return factors


def forecast_curve(
    *,
    profile: str,
    fuel: str,
    region_factor: float,
    estimate: LevelEstimate,
    start_tick: int,
    start_time: datetime,
    tick_minutes: int,
    horizon: int,
    level_factors: Sequence[float] | None = None,
) -> tuple[list[float], list[float], list[int]]:
    """Per-tick P50 demand and its standard deviation for ticks start_tick .. +horizon-1."""
    means, sds, ticks = [], [], []
    per_tick_sd = estimate.noise / math.sqrt(3)
    for k in range(horizon):
        t = start_tick + k
        hour = (start_time + timedelta(minutes=tick_minutes * k)).hour
        base = base_per_tick(profile, fuel, region_factor, hour)
        factor = level_factors[k] if level_factors else 1.0
        level = estimate.level * factor
        mean = level * base
        sd = base * factor * math.sqrt(estimate.level_se**2 + (estimate.level * per_tick_sd) ** 2)
        means.append(mean)
        sds.append(sd)
        ticks.append(t)
    return means, sds, ticks


def parse_sim_time(value: str) -> datetime:
    return datetime.fromisoformat(value)  # simulator times are naive UTC (SIMULATOR_NOTES #13)
