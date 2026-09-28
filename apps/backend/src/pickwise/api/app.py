# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory (composition root for the HTTP process)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pickwise import __version__
from pickwise.api import health
from pickwise.api.middleware import RequestContextMiddleware
from pickwise.platform.scanning import StubScanner
from pickwise.platform.scanning.stub import STUB_WARNING
from pickwise.platform.startup_checks import check_settings, run_startup_checks
from pickwise.shared.db import create_engine
from pickwise.shared.logging import configure_logging, get_logger
from pickwise.shared.settings import ScannerKind, Settings, get_settings

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # Fail fast on configuration alone, before any connection is opened.
    check_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        try:
            await run_startup_checks(settings, engine)
            if settings.scanner is ScannerKind.STUB:
                log.warning(STUB_WARNING, scanner=StubScanner.name)
            log.info("api started", environment=settings.pickwise_env.value)
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Pickwise API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app
