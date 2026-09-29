"""intelligence entrypoint: `uvicorn intelligence.main:create --factory --port 8090`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from intelligence.config import IntelligenceSettings
from intelligence.forecast.model import MODEL_VERSION
from intelligence.forecast.predict import PredictRequest, predict
from jalani_common.config import load_settings
from jalani_common.service import create_app, postgres_check, redis_check
from jalani_common.telemetry import configure_logging


def create(settings: IntelligenceSettings | None = None) -> FastAPI:
    settings = settings or load_settings(IntelligenceSettings)
    configure_logging("intelligence", settings.log_level)
    app = create_app(
        service="intelligence",
        settings=settings,
        # Forecasting is stateless (core-api sends the world it sees), so storage is optional.
        checks=[
            postgres_check(settings.database_url, critical=False),
            redis_check(settings.redis_url, critical=False),
        ],
        extra_version=lambda: {"model_version": MODEL_VERSION},
    )
    router = APIRouter(prefix="/v1")

    @router.post("/predict")
    def predict_endpoint(req: PredictRequest) -> dict[str, Any]:
        return predict(req)

    app.include_router(router)
    return app
