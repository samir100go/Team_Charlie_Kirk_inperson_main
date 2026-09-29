"""FastAPI scaffolding every JALANI service shares.

/healthz  liveness: the process is up and serving (never checks dependencies)
/readyz   readiness: probes dependencies. Critical ones gate readiness (503);
          non-critical ones only mark the service "degraded" (still 200),
          because we degrade gracefully instead of going down.
/metrics  Prometheus exposition
/version  build and model metadata (deployment versioning)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from jalani_common.config import CommonSettings
from jalani_common.telemetry import BUILD_INFO, DEPENDENCY_UP, instrument_http

Probe = Callable[[], Awaitable[str | None]]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class Check:
    """A readiness probe. `probe` raises on failure and may return a detail string."""

    name: str
    probe: Probe
    critical: bool = True


def postgres_check(dsn: str, *, critical: bool = True) -> Check:
    async def probe() -> str | None:
        import asyncpg

        conn = await asyncpg.connect(dsn, timeout=2)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        return None

    return Check("postgres", probe, critical)


def redis_check(url: str, *, critical: bool = True) -> Check:
    async def probe() -> str | None:
        from redis.asyncio import Redis

        client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return None

    return Check("redis", probe, critical)


def http_check(name: str, url: str, *, critical: bool = True) -> Check:
    async def probe() -> str | None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return None

    return Check(name, probe, critical)


async def run_checks(service: str, checks: Sequence[Check], timeout_s: float) -> dict[str, Any]:
    async def one(check: Check) -> tuple[str, dict[str, Any]]:
        started = time.perf_counter()
        try:
            detail = await asyncio.wait_for(check.probe(), timeout=timeout_s)
            result: dict[str, Any] = {"status": "up", "detail": detail}
        except TimeoutError:
            result = {"status": "down", "detail": f"timeout after {timeout_s}s"}
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            result = {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}
        result["critical"] = check.critical
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        DEPENDENCY_UP.labels(service, check.name, str(check.critical).lower()).set(
            1 if result["status"] == "up" else 0
        )
        return check.name, result

    results = dict(await asyncio.gather(*(one(c) for c in checks)))
    down = [name for name, r in results.items() if r["status"] == "down"]
    critical_down = [name for name in down if results[name]["critical"]]
    status = "not_ready" if critical_down else ("degraded" if down else "ready")
    return {"status": status, "checks": results}


def package_version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "0.0.0+unknown"


def create_app(
    *,
    service: str,
    settings: CommonSettings,
    checks: Sequence[Check] = (),
    lifespan: Lifespan | None = None,
    extra_version: Callable[[], dict[str, Any]] | None = None,
    dist_name: str | None = None,
) -> FastAPI:
    app_version = package_version(dist_name or f"jalani-{service}")

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        BUILD_INFO.labels(service, app_version, settings.git_sha, settings.image_tag).set(1)
        if lifespan is None:
            yield
        else:
            async with lifespan(app):
                yield

    app = FastAPI(title=f"JALANI {service}", version=app_version, lifespan=_lifespan)
    app.state.settings = settings
    app.state.service = service
    instrument_http(app, service)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> JSONResponse:
        report = await run_checks(service, checks, settings.readiness_timeout_s)
        report["service"] = service
        code = 503 if report["status"] == "not_ready" else 200
        return JSONResponse(report, status_code=code)

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/version", tags=["ops"])
    async def version_info() -> dict[str, Any]:
        info: dict[str, Any] = {
            "service": service,
            "version": app_version,
            "git_sha": settings.git_sha,
            "build_time": settings.build_time,
            "image_tag": settings.image_tag,
        }
        if extra_version is not None:
            info.update(extra_version())
        return info

    return app
