# SPDX-License-Identifier: AGPL-3.0-only
"""Liveness and readiness endpoints (unauthenticated, no tenant data)."""

import asyncio
from collections.abc import Awaitable
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pickwise.platform.storage import buckets_reachable
from pickwise.shared.logging import get_logger
from pickwise.shared.settings import Settings

router = APIRouter(tags=["health"])
log = get_logger(__name__)


class CheckStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"


class Liveness(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: Literal["ok"] = "ok"


class DependencyCheck(BaseModel):
    status: CheckStatus
    latency_ms: float = Field(description="Time the check took, in milliseconds")


class Readiness(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "environment": "development",
                    "scanner": "stub",
                    "checks": {
                        "database": {"status": "ok", "latency_ms": 2.1},
                        "object_storage": {"status": "ok", "latency_ms": 8.4},
                    },
                }
            ]
        }
    )

    status: CheckStatus
    environment: str
    scanner: str = Field(description="Active virus scanner: `stub` (dev only) or `clamav`")
    checks: dict[str, DependencyCheck]


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine_dep(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.get("/healthz", response_model=Liveness, summary="Liveness probe")
async def healthz() -> Liveness:
    return Liveness()


async def _timed(name: str, check: Awaitable[object], limit_seconds: float) -> DependencyCheck:
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        async with asyncio.timeout(limit_seconds):
            await check
        outcome = CheckStatus.OK
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        log.warning("readiness check failed", check=name, error=type(exc).__name__)
        outcome = CheckStatus.FAIL
    return DependencyCheck(status=outcome, latency_ms=round((loop.time() - started) * 1000, 1))


async def _database_ok(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@router.get(
    "/readyz",
    response_model=Readiness,
    summary="Readiness probe (database and object storage)",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def readyz(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    engine: Annotated[AsyncEngine, Depends(get_engine_dep)],
) -> Readiness:
    timeout = settings.readiness_timeout_seconds
    database, storage = await asyncio.gather(
        _timed("database", _database_ok(engine), timeout),
        _timed("object_storage", buckets_reachable(settings), timeout),
    )
    checks = {"database": database, "object_storage": storage}
    healthy = all(c.status is CheckStatus.OK for c in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status=CheckStatus.OK if healthy else CheckStatus.FAIL,
        environment=settings.pickwise_env.value,
        scanner=settings.scanner.value,
        checks=checks,
    )
