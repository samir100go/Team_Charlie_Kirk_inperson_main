from __future__ import annotations

from pydantic import Field, SecretStr

from jalani_common.config import CommonSettings


class CoreApiSettings(CommonSettings):
    simulator_url: str
    database_url: str
    redis_url: str
    intelligence_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    # Comma-separated list of browser origins allowed by CORS.
    cors_origins: str = "http://localhost:3000"
    autopilot_enabled: bool = True
