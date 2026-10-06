# SPDX-License-Identifier: AGPL-3.0-only
"""Revealing masked values: permission, audit (never the value), rate limit and caching."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pickwise.platform.auth.dependencies import Authorized
from pickwise.platform.reveal import REVEAL_FIELDS, RevealField
from pickwise.shared.errors import NotFoundError
from tests.integration.conftest import MakeApi
from tests.integration.support import Env, Tenant

pytestmark = pytest.mark.db

SECRET = "ABCDE1234F"
KNOWN = uuid.uuid4()


async def _pan(_db: AsyncSession, _auth: Authorized, entity_id: uuid.UUID) -> str | None:
    if entity_id != KNOWN:
        raise NotFoundError("Not found.")
    return SECRET


@pytest.fixture(autouse=True)
def stub_field() -> Iterator[None]:
    REVEAL_FIELDS.register(
        RevealField("thing", "pan", "platform.custom_fields", "platform.audit.read", _pan)
    )
    yield
    REVEAL_FIELDS.unregister("thing", "pan")


def body(**overrides: object) -> dict[str, object]:
    return {"entity_type": "thing", "entity_id": str(KNOWN), "field": "pan", **overrides}


async def test_reveal_returns_the_value_uncached_and_audits_without_it(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    response = await admin.post("/v1/pii/reveal", body())
    assert response.status_code == 200, response.text
    assert response.json() == {"value": SECRET}
    assert response.headers["cache-control"] == "no-store"

    rows = await env.sql(
        "SELECT changes::text, actor_user_id FROM audit.events "
        "WHERE tenant_id = :t AND action = 'pii.reveal'",
        t=tenant.id,
    )
    assert len(rows) == 1
    assert SECRET not in rows[0][0]
    assert '"field": "pan"' in rows[0][0]
    assert rows[0][1] == tenant.admin.user_id


async def test_reveal_needs_the_fields_permission(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    employee = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    response = await employee.post("/v1/pii/reveal", body())
    assert response.status_code == 403
    assert SECRET not in response.text
    rows = await env.sql(
        "SELECT count(*) FROM audit.events WHERE tenant_id = :t AND action = 'pii.reveal'",
        t=tenant.id,
    )
    assert rows[0][0] == 0


async def test_unknown_fields_and_hidden_records_are_not_found(
    make_api: MakeApi, tenant: Tenant
) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    assert (await admin.post("/v1/pii/reveal", body(field="aadhaar"))).status_code == 404
    assert (await admin.post("/v1/pii/reveal", body(entity_type="nothing"))).status_code == 404
    assert (
        await admin.post("/v1/pii/reveal", body(entity_id=str(uuid.uuid4())))
    ).status_code == 404
    assert (await admin.post("/v1/pii/reveal", body(field="Bad Field"))).status_code == 422


async def test_reveal_is_rate_limited(make_api: MakeApi, tenant: Tenant) -> None:
    admin = await (await make_api()).sign_in(tenant.admin)
    codes = [(await admin.post("/v1/pii/reveal", body())).status_code for _ in range(32)]
    assert codes[:30] == [200] * 30
    assert 429 in codes[30:]


async def test_reveal_needs_a_session(make_api: MakeApi) -> None:
    api = await make_api()
    assert (await api.post("/v1/pii/reveal", body())).status_code in (401, 403)
