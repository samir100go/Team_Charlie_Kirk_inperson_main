"""core-api entrypoint: `uvicorn core_api.main:create --factory --port 8080`."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from core_api.auth.tokens import Auth, auth_router
from core_api.config import CoreApiSettings
from core_api.slice import REQUEST_WINDOW, Slice, lifespan_for
from core_api.slice import router as slice_router
from core_api.store import Store
from jalani_common.config import load_settings
from jalani_common.service import create_app, http_check, postgres_check, redis_check
from jalani_common.telemetry import configure_logging


def create(settings: CoreApiSettings | None = None) -> FastAPI:
    settings = settings or load_settings(CoreApiSettings)
    configure_logging("core-api", settings.log_level)
    world = Slice(
        settings.simulator_url,
        settings.intelligence_url,
        store=Store(settings.database_url),
        redis_url=settings.redis_url,
    )
    app = create_app(
        service="core-api",
        settings=settings,
        lifespan=lifespan_for(world),
        # Every dependency is soft: core-api degrades (fallback policy, cached state,
        # buffered audit writes) instead of going down (brief §11).
        checks=[
            postgres_check(settings.database_url, critical=False),
            redis_check(settings.redis_url, critical=False),
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
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def _window(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        started = time.perf_counter()
        error = True
        try:
            response = await call_next(request)
            error = response.status_code >= 500
            return response
        finally:
            REQUEST_WINDOW.add(time.perf_counter() - started, error)

    auth = Auth(
        settings.jwt_secret.get_secret_value(),
        {
            "operator": (settings.operator_password.get_secret_value(), "operator"),
            "admin": (settings.admin_password.get_secret_value(), "admin"),
        },
    )
    app.include_router(auth_router(auth))
    app.include_router(slice_router(world, auth, chaos_enabled=settings.chaos_enabled))
    return app
