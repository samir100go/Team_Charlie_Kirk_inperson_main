"""Ingestor entrypoint: `uvicorn ingestor.main:create --factory --port 8070`."""

from __future__ import annotations

from fastapi import FastAPI

from ingestor.config import IngestorSettings
from jalani_common.config import load_settings
from jalani_common.service import create_app, http_check, postgres_check, redis_check
from jalani_common.telemetry import configure_logging


def create(settings: IngestorSettings | None = None) -> FastAPI:
    settings = settings or load_settings(IngestorSettings)
    configure_logging("ingestor", settings.log_level)
    return create_app(
        service="ingestor",
        settings=settings,
        checks=[
            postgres_check(settings.database_url),
            redis_check(settings.redis_url),
            # Nothing to ingest without the simulator: critical for the ingestor.
            http_check("simulator", f"{settings.simulator_url.rstrip('/')}/v1/health"),
        ],
    )
