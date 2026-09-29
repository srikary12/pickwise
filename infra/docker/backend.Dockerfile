# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: AGPL-3.0-only
# Backend image: API, worker, migrate and CLI all run from it.
# Targets: dev (hot reload, dev deps) and prod (slim, non-root, no dev deps).
# Build context: repository root.

ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /uvx /usr/local/bin/
RUN groupadd --system --gid 10001 pickwise \
 && useradd --system --uid 10001 --gid pickwise --home-dir /app --shell /usr/sbin/nologin pickwise
WORKDIR /app/apps/backend

# --- dev: all dependency groups, editable install; src/ and db/ are bind-mounted in compose.dev
FROM base AS dev
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY apps/backend/ ./
COPY db/ /app/db/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
USER pickwise
EXPOSE 8000
CMD ["uvicorn", "pickwise.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# --- build: runtime dependencies + the project as a non-editable wheel
FROM base AS build
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY apps/backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- prod: no uv, no sources beyond what's needed at runtime
FROM ${PYTHON_IMAGE} AS prod
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --system --gid 10001 pickwise \
 && useradd --system --uid 10001 --gid pickwise --home-dir /app --shell /usr/sbin/nologin pickwise
COPY --from=build /opt/venv /opt/venv
COPY apps/backend/alembic.ini /app/apps/backend/alembic.ini
COPY db/migrations /app/db/migrations
COPY db/pii_classification.yaml /app/db/pii_classification.yaml
WORKDIR /app/apps/backend
USER pickwise
EXPOSE 8000
CMD ["uvicorn", "pickwise.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
