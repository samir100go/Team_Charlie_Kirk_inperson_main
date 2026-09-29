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


def test_intelligence_and_simulator_are_soft_dependencies() -> None:
    with TestClient(create(CoreApiSettings(**UNREACHABLE))) as client:
        checks = client.get("/readyz").json()["checks"]
    assert checks["postgres"]["critical"] is True
    assert checks["redis"]["critical"] is True
    assert checks["intelligence"]["critical"] is False
    assert checks["simulator"]["critical"] is False


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
