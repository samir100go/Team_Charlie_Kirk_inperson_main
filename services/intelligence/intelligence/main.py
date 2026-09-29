"""intelligence entrypoint: `uvicorn intelligence.main:create --factory --port 8090`."""

from __future__ import annotations

from fastapi import FastAPI

from intelligence.config import IntelligenceSettings
from jalani_common.config import load_settings
from jalani_common.service import create_app, postgres_check, redis_check
from jalani_common.telemetry import configure_logging


def create(settings: IntelligenceSettings | None = None) -> FastAPI:
    settings = settings or load_settings(IntelligenceSettings)
    configure_logging("intelligence", settings.log_level)
    return create_app(
        service="intelligence",
        settings=settings,
        checks=[
            postgres_check(settings.database_url),
            redis_check(settings.redis_url, critical=False),
        ],
        extra_version=lambda: {"model_version": settings.model_version},
    )
