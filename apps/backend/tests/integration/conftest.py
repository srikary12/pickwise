# SPDX-License-Identifier: AGPL-3.0-only
import os
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from pickwise.api.app import create_app
from pickwise.platform.crypto import kek_from_settings
from pickwise.platform.jobs import set_job_queue
from pickwise.platform.notifications import email as email_module
from pickwise.platform.notifications.email import set_dispatcher
from pickwise.shared.db import Database
from pickwise.shared.settings import Settings
from tests.integration.support import Api, Env, Tenant

pytestmark = pytest.mark.db

MakeApi = Callable[[], Awaitable[Api]]


@pytest.fixture(scope="session")
def api_settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
async def worker_db() -> AsyncIterator[Database]:
    db = Database.from_settings(
        Settings(
            database_user="pickwise_worker",
            database_password=SecretStr(os.environ["PG_WORKER_PASSWORD"]),
        )
    )
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def app(api_settings: Settings, worker_db: Database) -> AsyncIterator[FastAPI]:
    application = create_app(api_settings, enqueue_jobs=False)
    # A transport-less client is replaced per test that needs an IdP (see test_sso).
    application.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    async with application.router.lifespan_context(application):
        yield application
    await application.state.http_client.aclose()


@pytest.fixture(scope="session")
async def env(api_settings: Settings, worker_db: Database, app: FastAPI) -> AsyncIterator[Env]:
    environment = Env(api_settings, worker_db, kek_from_settings(api_settings), app)
    patch = pytest.MonkeyPatch()
    patch.setattr(email_module, "_smtp_send", environment.mailbox.smtp)
    set_dispatcher(environment.mailbox.dispatcher)
    set_job_queue(environment.defer_job)
    yield environment
    set_dispatcher(None)
    set_job_queue(None)
    patch.undo()
    await environment.purge_all()


@pytest.fixture
async def make_api(app: FastAPI, api_settings: Settings) -> AsyncIterator[MakeApi]:
    clients: list[Api] = []

    async def factory() -> Api:
        api = await Api(app, api_settings).with_csrf()
        clients.append(api)
        return api

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
async def api(make_api: MakeApi) -> Api:
    return await make_api()


@pytest.fixture
async def tenant(env: Env) -> Tenant:
    return await env.create_tenant()
