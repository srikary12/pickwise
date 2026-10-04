# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory (composition root for the HTTP process)."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from pickwise import __version__, wiring
from pickwise.api import health
from pickwise.api.middleware import (
    ClientIpMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from pickwise.platform.admin.router import router as admin_router
from pickwise.platform.approvals.router import router as approvals_router
from pickwise.platform.auth.dependencies import csrf_protect
from pickwise.platform.auth.router import router as auth_router
from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.custom_fields.router import router as custom_fields_router
from pickwise.platform.files.router import router as files_router
from pickwise.platform.jobs import set_job_queue
from pickwise.platform.notifications.email import QueuedEmail, set_dispatcher
from pickwise.platform.notifications.router import router as notifications_router
from pickwise.platform.provisioning.router import router as signup_router
from pickwise.platform.scanning import StubScanner
from pickwise.platform.scanning.stub import STUB_WARNING
from pickwise.platform.startup_checks import check_settings, run_startup_checks
from pickwise.platform.webhooks.router import router as webhooks_router
from pickwise.shared.db import Database
from pickwise.shared.errors import AppError
from pickwise.shared.logging import configure_logging, get_logger
from pickwise.shared.settings import ScannerKind, Settings, get_settings

log = get_logger(__name__)


async def _defer_job(task_name: str, **kwargs: str | None) -> None:
    from pickwise.worker.app import app as jobs_app

    await jobs_app.configure_task(task_name).defer_async(**kwargs)


async def _defer_email(email: QueuedEmail) -> None:
    from pickwise.worker.tasks import send_email

    await send_email.defer_async(
        outbox_id=str(email.outbox_id),
        tenant_id=str(email.tenant_id) if email.tenant_id else None,
    )


def create_app(settings: Settings | None = None, *, enqueue_jobs: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # Fail fast on configuration alone, before any connection is opened.
    check_settings(settings)
    wiring.register_all()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database.from_settings(settings)
        app.state.settings = settings
        app.state.database = database
        app.state.engine = database.engine
        app.state.kek = (
            kek_from_settings(settings) if settings.pickwise_kek.get_secret_value() else None
        )
        async with AsyncExitStack() as stack:
            stack.push_async_callback(database.dispose)
            # Outbound calls (OIDC providers); tests swap in a mock transport.
            if not hasattr(app.state, "http_client"):
                app.state.http_client = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=10, follow_redirects=False)
                )
            await run_startup_checks(settings, database.engine)
            if enqueue_jobs:
                from pickwise.worker.app import app as jobs

                await stack.enter_async_context(jobs.open_async())
                set_dispatcher(_defer_email)
                set_job_queue(_defer_job)
                stack.callback(set_dispatcher, None)
                stack.callback(set_job_queue, None)
            if settings.scanner is ScannerKind.STUB:
                log.warning(STUB_WARNING, scanner=StubScanner.name)
            log.info("api started", environment=settings.pickwise_env.value)
            yield

    app = FastAPI(
        title="Pickwise API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        dependencies=[Depends(csrf_protect)],
    )

    app.state.settings = settings

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.message, **exc.details}}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    # Starlette runs middleware in reverse order of addition: request id first.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.secure_cookies)
    app.add_middleware(ClientIpMiddleware, trusted=settings.trusted_proxy_networks())
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(signup_router)
    app.include_router(files_router)
    app.include_router(custom_fields_router)
    app.include_router(notifications_router)
    app.include_router(approvals_router)
    app.include_router(webhooks_router)
    return app
