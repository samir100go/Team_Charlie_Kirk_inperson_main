from __future__ import annotations

from jalani_common.config import CommonSettings


class IntelligenceSettings(CommonSettings):
    database_url: str
    redis_url: str
    # Reported on /version until the model registry (Phase 4.1) takes over.
    model_version: str = "structural-prior-v0"
