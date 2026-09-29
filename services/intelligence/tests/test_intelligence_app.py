from __future__ import annotations

from fastapi.testclient import TestClient

from intelligence.config import IntelligenceSettings
from intelligence.main import create


def test_version_includes_model_version() -> None:
    settings = IntelligenceSettings(
        database_url="postgresql://x@127.0.0.1:9/x",
        redis_url="redis://127.0.0.1:9/0",
        model_version="prior-test",
        readiness_timeout_s=0.5,
    )
    with TestClient(create(settings)) as client:
        assert client.get("/version").json()["model_version"] == "prior-test"
        checks = client.get("/readyz").json()["checks"]
    assert checks["redis"]["critical"] is False
