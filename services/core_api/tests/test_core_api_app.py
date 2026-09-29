from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core_api.config import CoreApiSettings
from core_api.main import create

UNREACHABLE: dict[str, Any] = dict(
    simulator_url="http://127.0.0.1:9", database_url="postgresql://x@127.0.0.1:9/x",
    redis_url="redis://127.0.0.1:9/0", intelligence_url="http://127.0.0.1:9",
    jwt_secret="x" * 32, readiness_timeout_s=0.5,
)  # fmt: skip


def test_every_dependency_is_soft_so_core_api_degrades_instead_of_failing() -> None:
    with TestClient(create(CoreApiSettings(**UNREACHABLE))) as client:
        ready = client.get("/readyz")
    checks = ready.json()["checks"]
    assert all(c["critical"] is False for c in checks.values())
    assert set(checks) == {"postgres", "redis", "intelligence", "simulator"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"


def test_cors_allows_only_configured_origin() -> None:
    settings = CoreApiSettings(**{**UNREACHABLE, "cors_origins": "http://localhost:3000"})
    with TestClient(create(settings)) as client:
        ok = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
        evil = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in evil.headers


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CoreApiSettings(**{**UNREACHABLE, "jwt_secret": "too-short"})
