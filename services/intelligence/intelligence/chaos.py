"""Prediction-service outage switch for the resilience demo (only when CHAOS_ENABLED=true).

While an outage is active /v1/predict answers 503, so core-api experiences a real failed
dependency and must switch to its fallback allocation policy (brief §11).
"""

from __future__ import annotations

import time

import structlog

log = structlog.get_logger()


class Outage:
    def __init__(self) -> None:
        self.until = 0.0

    def active(self) -> bool:
        return time.time() < self.until

    def start(self, seconds: int) -> None:
        self.until = time.time() + seconds
        log.warning("chaos.prediction_outage", seconds=seconds)

    def clear(self) -> None:
        self.until = 0.0
        log.info("chaos.prediction_outage_cleared")

    def remaining(self) -> float:
        return max(0.0, round(self.until - time.time(), 1))
