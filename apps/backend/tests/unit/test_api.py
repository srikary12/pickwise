# SPDX-License-Identifier: AGPL-3.0-only
import uuid

import httpx
import pytest
from fastapi import FastAPI

from pickwise.api.app import create_app
from pickwise.shared.settings import Settings


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings())


@pytest.fixture
async def client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_every_response_gets_a_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    uuid.UUID(response.headers["X-Request-ID"])


async def test_safe_incoming_request_id_is_kept(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "trace-123.abc"})
    assert response.headers["X-Request-ID"] == "trace-123.abc"


async def test_unsafe_incoming_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "x" * 200})
    assert response.headers["X-Request-ID"] != "x" * 200
    uuid.UUID(response.headers["X-Request-ID"])


def test_openapi_documents_readiness_with_examples(app: FastAPI) -> None:
    schema = app.openapi()
    readyz = schema["paths"]["/readyz"]["get"]["responses"]
    assert {"200", "503"} <= set(readyz)
    assert schema["components"]["schemas"]["Readiness"]["examples"]
