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
    operator_password="operator-test-pw", admin_password="admin-test-pw-1",
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


def test_login_roles_protect_sensitive_actions() -> None:
    settings = CoreApiSettings(**{**UNREACHABLE, "chaos_enabled": True})
    with TestClient(create(settings)) as client:
        bad = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        op = client.post(
            "/api/v1/auth/login", json={"username": "operator", "password": "operator-test-pw"}
        ).json()
        admin = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-test-pw-1"}
        ).json()
        anon_approve = client.post("/api/v1/recommendations/t1-x/approve")
        op_headers = {"Authorization": f"Bearer {op['token']}"}
        op_chaos = client.post("/api/v1/chaos/clear", headers=op_headers)
        op_approve = client.post("/api/v1/recommendations/t1-x/approve", headers=op_headers)
        admin_me = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {admin['token']}"}
        ).json()
        forged = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x.y.z"})
        bad_id = client.post("/api/v1/recommendations/..%2F..%2Fetc/approve", headers=op_headers)
    assert bad.status_code == 401
    assert op["role"] == "operator"
    assert anon_approve.status_code == 401  # approving needs a login
    assert op_chaos.status_code == 403  # injecting failures needs the admin role
    assert op_approve.status_code == 404  # authorised; the recommendation just does not exist
    assert admin_me == {"username": "admin", "role": "admin"}
    assert forged.status_code == 401
    assert bad_id.status_code in (404, 422)
