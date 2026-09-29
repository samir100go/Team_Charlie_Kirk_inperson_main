from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ingestor.config import IngestorSettings
from ingestor.main import create
from jalani_common.config import ConfigError

UNREACHABLE = dict(
    simulator_url="http://127.0.0.1:9", database_url="postgresql://x@127.0.0.1:9/x",
    redis_url="redis://127.0.0.1:9/0", readiness_timeout_s=0.5,
)  # fmt: skip


def test_serves_ops_endpoints_and_reports_all_dependencies_critical() -> None:
    with TestClient(create(IngestorSettings(**UNREACHABLE))) as client:
        assert client.get("/healthz").json()["service"] == "ingestor"
        ready = client.get("/readyz")
    assert ready.status_code == 503
    checks = ready.json()["checks"]
    assert set(checks) == {"postgres", "redis", "simulator"}
    assert all(c["critical"] for c in checks.values())


def test_missing_env_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SIMULATOR_URL", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ConfigError, match="SIMULATOR_URL: missing"):
        create()
