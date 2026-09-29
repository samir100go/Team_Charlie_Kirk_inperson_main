"""Environment-driven settings shared by every JALANI service.

Every variable is documented in `.env.example`. Services subclass
`CommonSettings`; fields without a default are required, and `load_settings`
turns a missing or invalid variable into one clear startup error.
"""

from __future__ import annotations

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(SystemExit):
    """Raised at startup when required configuration is missing or invalid."""


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    log_level: str = "INFO"
    # Build metadata, injected by the Dockerfile / CI (see /version).
    git_sha: str = "unknown"
    build_time: str = "unknown"
    image_tag: str = "dev"
    # /readyz probes each dependency with this timeout.
    readiness_timeout_s: float = Field(default=2.0, gt=0)
    # Internal /chaos endpoints (Phase 7) stay disabled unless explicitly enabled.
    chaos_enabled: bool = False


def load_settings[S: BaseSettings](cls: type[S]) -> S:
    """Instantiate settings, failing fast with the env var names that are wrong."""
    try:
        return cls()
    except ValidationError as exc:
        problems = []
        for err in exc.errors(include_url=False):
            var = "_".join(str(part) for part in err["loc"]).upper()
            reason = "missing" if err["type"] == "missing" else err["msg"]
            problems.append(f"  {var}: {reason}")
        raise ConfigError(
            f"{cls.__name__}: invalid configuration\n"
            + "\n".join(problems)
            + "\nSet these in the environment or .env (documented in .env.example)."
        ) from None
