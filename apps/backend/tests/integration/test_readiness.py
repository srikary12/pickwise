# SPDX-License-Identifier: AGPL-3.0-only
import httpx
import pytest

from pickwise.api.app import create_app
from pickwise.shared.settings import Settings

pytestmark = [pytest.mark.db, pytest.mark.s3]


async def test_readyz_reports_database_and_storage_ok() -> None:
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["object_storage"]["status"] == "ok"


async def test_readyz_reports_503_when_storage_is_misconfigured() -> None:
    app = create_app(Settings(s3_bucket_files="pickwise-no-such-bucket"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["object_storage"]["status"] == "fail"
