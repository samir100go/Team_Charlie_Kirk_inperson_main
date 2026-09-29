"""core-api entrypoint: `uvicorn core_api.main:create --factory --port 8080`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core_api.config import CoreApiSettings
from jalani_common.config import load_settings
from jalani_common.service import create_app, http_check, postgres_check, redis_check
from jalani_common.telemetry import configure_logging


def create(settings: CoreApiSettings | None = None) -> FastAPI:
    settings = settings or load_settings(CoreApiSettings)
    configure_logging("core-api", settings.log_level)
    app = create_app(
        service="core-api",
        settings=settings,
        checks=[
            postgres_check(settings.database_url),
            redis_check(settings.redis_url),
            # Soft dependencies: core-api degrades (fallback policy, cached state)
            # instead of going down when these are unavailable.
            http_check(
                "intelligence", f"{settings.intelligence_url.rstrip('/')}/healthz", critical=False
            ),
            http_check(
                "simulator", f"{settings.simulator_url.rstrip('/')}/v1/health", critical=False
            ),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app
