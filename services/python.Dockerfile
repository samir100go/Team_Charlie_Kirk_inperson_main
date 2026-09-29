# syntax=docker/dockerfile:1.7
# Shared multi-stage image for the Python services. Build from the repo root:
#   docker build -f services/python.Dockerfile --build-arg SERVICE=core_api \
#     --build-arg PACKAGE=jalani-core-api --build-arg PORT=8080 -t jalani/core-api .
ARG PYTHON_IMAGE=python:3.12.14-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.20

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
ARG SERVICE
ARG PACKAGE
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# 1) Third-party dependencies only: cached until the lockfile or a pyproject changes.
COPY pyproject.toml uv.lock ./
COPY services/common/pyproject.toml services/common/
COPY services/core_api/pyproject.toml services/core_api/
COPY services/ingestor/pyproject.toml services/ingestor/
COPY services/intelligence/pyproject.toml services/intelligence/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package "${PACKAGE}"
# 2) Our code, installed non-editable so the runtime stage needs nothing but the venv.
COPY services/common services/common
COPY services/${SERVICE} services/${SERVICE}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package "${PACKAGE}"

FROM ${PYTHON_IMAGE} AS runtime
ARG SERVICE
ARG PORT=8080
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG IMAGE_TAG=dev
RUN groupadd --system --gid 10001 jalani \
 && useradd --system --uid 10001 --gid jalani --home-dir /app --shell /usr/sbin/nologin jalani
WORKDIR /app
COPY --from=builder --chown=jalani:jalani /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 \
    APP_MODULE="${SERVICE}.main:create" PORT="${PORT}" \
    GIT_SHA="${GIT_SHA}" BUILD_TIME="${BUILD_TIME}" IMAGE_TAG="${IMAGE_TAG}"
USER jalani
EXPOSE ${PORT}
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'], timeout=2)"
CMD ["sh", "-c", "exec uvicorn \"$APP_MODULE\" --factory --host 0.0.0.0 --port \"$PORT\""]
