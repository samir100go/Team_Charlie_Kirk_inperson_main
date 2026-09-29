"""Forecast, anomaly detection and /v1/predict on real recorded simulator data.

docs/experiments/raw/e1_demand_rows.json is 96 ticks (one simulated day) of demand rows
recorded from the live simulator in Phase 0 (no allocations, no events).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import simfixtures as fx
from intelligence.config import IntelligenceSettings
from intelligence.forecast.model import estimate_level
from intelligence.forecast.predict import PredictRequest, predict
from intelligence.main import create

ROWS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/experiments/raw/e1_demand_rows.json").read_text()
)
REGIONS = {r["id"]: r["demand_factor"] for r in fx.body("regions__ok")}


def _request(rows: list[dict[str, Any]], tick: int, **extra: Any) -> dict[str, Any]:
    sim_hour, sim_min = divmod(tick * 15, 60)
    return {
        "tick": tick,
        "tick_minutes": 15,
        "sim_time": f"2026-01-01T{sim_hour % 24:02d}:{sim_min:02d}:00",
        "horizon_ticks": 96,
        "regions": REGIONS,
        "stations": fx.body("stations__ok"),
        "demand": [r for r in rows if r["tick"] < tick],
        **extra,
    }


def _pred(out: dict[str, Any], station: str, fuel: str) -> dict[str, Any]:
    return next(p for p in out["predictions"] if p["station_id"] == station and p["fuel"] == fuel)


def test_normal_operations_level_is_one_and_no_anomaly() -> None:
    out = predict(PredictRequest.model_validate(_request(ROWS, 40)))
    assert len(out["predictions"]) == 12
    for p in out["predictions"]:
        assert p["level"] == pytest.approx(1.0, abs=0.06), p["station_id"]
        assert not p["anomaly"]["detected"]
        assert p["confidence"] >= 0.8


def test_one_tick_ahead_error_on_real_data_is_within_the_noise() -> None:
    errors = []
    for tick in range(16, 96):
        out = predict(PredictRequest.model_validate(_request(ROWS, tick)))
        actual = {(r["station_id"], r["fuel_type"]): r["demand_liters"]
                  for r in ROWS if r["tick"] == tick}  # fmt: skip
        for p in out["predictions"]:
            a = actual[(p["station_id"], p["fuel"])]
            errors.append(abs(p["curve_mean"][0] - a) / a)
    mape = sum(errors) / len(errors)
    # documented noise is uniform +-8..12 %: E|U| = noise/2 ~ 5 %, the floor for any model
    assert mape < 0.07, mape


def test_spike_is_detected_and_the_level_adapts() -> None:
    spiked = copy.deepcopy(ROWS)
    for r in spiked:
        if r["station_id"] == "station-mirpur" and r["tick"] >= 30:
            r["demand_liters"] *= 3  # what a demand_spike x3 does to observed demand
    out = predict(PredictRequest.model_validate(_request(spiked, 33)))
    p = _pred(out, "station-mirpur", "PETROL")
    assert p["anomaly"]["detected"]
    assert p["anomaly"]["direction"] == "spike"
    assert p["anomaly"]["since_tick"] == 30
    assert p["level"] == pytest.approx(3.0, rel=0.1)
    assert p["confidence"] < 0.6  # 3 ticks into a new regime: human review
    assert not _pred(out, "station-tongi", "PETROL")["anomaly"]["detected"]


def test_regime_level_uses_only_the_current_regime() -> None:
    ratios = [(t, 1.0) for t in range(20)] + [(t, 3.0) for t in range(20, 30)]
    est = estimate_level(ratios, 0.10)
    assert est.level == pytest.approx(3.0)
    assert est.n == 10
    assert est.anomaly.since_tick == 20


def test_known_spike_end_lowers_the_forecast_after_its_end_tick() -> None:
    spiked = copy.deepcopy(ROWS)
    for r in spiked:
        if r["station_id"] == "station-mirpur" and r["tick"] >= 20:
            r["demand_liters"] *= 2
    event = {"id": 1, "type": "demand_spike", "start_tick": 20, "end_tick": 44,
             "status": "ACTIVE", "parameters": {"station_ids": ["station-mirpur"],
                                                "multiplier": 2}}  # fmt: skip
    out = predict(PredictRequest.model_validate(_request(spiked, 40, events=[event])))
    p = _pred(out, "station-mirpur", "DIESEL")
    assert p["event_adjusted"]
    # tick 44 is the last spiked tick (k=4); tick 45 (k=5) is back to the normal level
    assert p["curve_mean"][5] < 0.6 * p["curve_mean"][4]


def test_incoming_fuel_delays_the_stockout() -> None:
    # Tongi burns ~14,000 L DIESEL a day: only a large arrival changes the 24 h risk.
    low = copy.deepcopy(fx.body("stations__ok"))
    for s in low:
        s["inventory"]["DIESEL"] = 800.0
    base = _request(ROWS, 40, stations=low)
    before = _pred(predict(PredictRequest.model_validate(base)), "station-tongi", "DIESEL")
    base["incoming"] = [{"station_id": "station-tongi", "fuel_type": "DIESEL",
                         "arrival_tick": 41, "quantity": 15000}]  # fmt: skip
    after = _pred(predict(PredictRequest.model_validate(base)), "station-tongi", "DIESEL")
    assert before["stockout"]["hours_p50"] == 1.0  # 800 L lasts ~4 ticks at peak hours
    assert after["stockout"]["hours_p50"] is None  # no stockout inside the 24 h horizon
    assert after["stockout"]["stockout_prob"] < 0.05 < before["stockout"]["stockout_prob"]


def test_api_validates_input() -> None:
    settings = IntelligenceSettings(
        database_url="postgresql://x@127.0.0.1:9/x", redis_url="redis://127.0.0.1:9/0"
    )
    with TestClient(create(settings)) as client:
        ok = client.post("/v1/predict", json=_request(ROWS, 24))
        bad_rows = [{**ROWS[0], "demand_liters": -5}]
        bad = client.post("/v1/predict", json=_request(bad_rows, 24))
    assert ok.status_code == 200
    assert len(ok.json()["predictions"]) == 12
    assert bad.status_code == 422
