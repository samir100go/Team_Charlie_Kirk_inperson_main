from __future__ import annotations

from pydantic import Field

from jalani_common.config import CommonSettings


class IngestorSettings(CommonSettings):
    simulator_url: str
    database_url: str
    redis_url: str
    # Polling fallback cadence when SSE is unavailable (Phase 2.2).
    poll_interval_ms: int = Field(default=500, gt=0)
