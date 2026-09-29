from __future__ import annotations

from jalani_common.config import CommonSettings


class IntelligenceSettings(CommonSettings):
    database_url: str
    redis_url: str
