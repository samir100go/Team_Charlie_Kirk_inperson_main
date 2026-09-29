"""Logging and HTTP metrics shared by every JALANI service.

Logs are JSON lines (structlog) so Loki can index `service`, `level` and
`event`. HTTP RED metrics are recorded per route template, not per raw path,
to keep label cardinality bounded.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "jalani_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["service", "method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "jalani_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["service", "method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0),
)
DEPENDENCY_UP = Gauge(
    "jalani_dependency_up",
    "1 if the dependency passed its last readiness probe, else 0.",
    ["service", "dependency", "critical"],
)
BUILD_INFO = Gauge(
    "jalani_build_info",
    "Always 1; labels carry the running build.",
    ["service", "version", "git_sha", "image_tag"],
)

# Probes and scrapes would drown the RED metrics of real traffic.
UNINSTRUMENTED_PATHS = frozenset({"/metrics", "/healthz", "/readyz"})


def configure_logging(service: str, level: str = "INFO") -> None:
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=numeric, format="%(message)s")
    # httpx logs every request at INFO, which would drown readiness probes in noise.
    logging.getLogger("httpx").setLevel(max(numeric, logging.WARNING))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(**initial: Any) -> Any:
    return structlog.get_logger(**initial)


def instrument_http(app: FastAPI, service: str) -> None:
    @app.middleware("http")
    async def _record(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in UNINSTRUMENTED_PATHS:
            return await call_next(request)
        started = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            HTTP_REQUESTS.labels(service, request.method, template, status).inc()
            HTTP_LATENCY.labels(service, request.method, template).observe(
                time.perf_counter() - started
            )
