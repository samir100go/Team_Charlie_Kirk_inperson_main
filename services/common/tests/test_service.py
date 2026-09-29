from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jalani_common.config import CommonSettings, ConfigError, load_settings
from jalani_common.service import Check, create_app


async def _up() -> str | None:
    return None


async def _down() -> str | None:
    raise ConnectionError("refused")


def _client(*checks: Check) -> TestClient:
    app = create_app(service="test", settings=CommonSettings(), checks=checks, dist_name="x")
    return TestClient(app)


def test_healthz_never_checks_dependencies() -> None:
    with _client(Check("db", _down)) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "test"}


def test_readyz_ready_when_all_up() -> None:
    with _client(Check("db", _up), Check("cache", _up)) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_readyz_503_when_critical_dependency_down() -> None:
    with _client(Check("db", _down), Check("cache", _up)) as client:
        resp = client.get("/readyz")
    body = resp.json()
    assert resp.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["db"]["status"] == "down"
    assert "ConnectionError" in body["checks"]["db"]["detail"]


def test_readyz_degraded_but_200_when_only_soft_dependency_down() -> None:
    with _client(Check("db", _up), Check("intelligence", _down, critical=False)) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_readyz_probe_timeout_marks_down() -> None:
    async def _hang() -> str | None:
        import asyncio

        await asyncio.sleep(5)
        return None

    app = create_app(
        service="test",
        settings=CommonSettings(readiness_timeout_s=0.05),
        checks=[Check("slow", _hang)],
        dist_name="x",
    )
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["slow"]["detail"].startswith("timeout")


def test_version_reports_build_metadata() -> None:
    settings = CommonSettings(git_sha="abc123", image_tag="v1.2.3", build_time="2026-09-29")
    app = create_app(
        service="test", settings=settings, dist_name="x", extra_version=lambda: {"model": "m1"}
    )
    with TestClient(app) as client:
        body = client.get("/version").json()
    assert body["git_sha"] == "abc123"
    assert body["image_tag"] == "v1.2.3"
    assert body["model"] == "m1"


def test_metrics_exposes_red_metrics_by_route_template() -> None:
    app = create_app(service="red", settings=CommonSettings(), dist_name="x")

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    with TestClient(app) as client:
        client.get("/items/1")
        client.get("/items/2")
        text = client.get("/metrics").text
    assert (
        'jalani_http_requests_total{method="GET",route="/items/{item_id}",'
        'service="red",status="200"} 2.0'
    ) in text
    assert 'route="/metrics"' not in text


def test_load_settings_names_missing_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    class NeedsDb(CommonSettings):
        database_url: str
        redis_url: str

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://x")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(NeedsDb)
    message = str(exc_info.value)
    assert "DATABASE_URL: missing" in message
    assert "REDIS_URL" not in message
    assert ".env.example" in message
